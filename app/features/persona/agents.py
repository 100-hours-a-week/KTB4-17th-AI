"""LLM과 말하는 유일한 곳.

프롬프트 - 세 가지 역할:
  - ConversationAgent : 다음 발화 생성 (하루)
  - TaggingAgent      : 턴별 경량 판정 (어떤 차원이 채워졌나)
  - ExtractionAgent   : 대화 전체 → 점수

대화와 추출을 한 호출에 합치지 않는다. 합치면 대화하느라 추출이
대충 되고, 추출 신경 쓰느라 말투가 딱딱해진다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass

from anthropic import AsyncAnthropic
from pydantic import ValidationError

from .schemas import (
    ALL_DIMENSIONS,
    SCORED,
    TEXTUAL,
    RawExtraction,
    Tags,
    Topic,
)

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
_client = AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LLMError(Exception):
    """호출 실패. 호출부가 잡아서 폴백한다."""


async def _call(*, system: str, messages: list[dict], max_tokens: int, timeout: float) -> str:
    try:
        resp = await asyncio.wait_for(
            _client.messages.create(model=MODEL, max_tokens=max_tokens, system=system, messages=messages),
            timeout=timeout,
        )
    except TimeoutError as e:
        raise LLMError(f"timeout after {timeout}s") from e
    except Exception as e:
        raise LLMError(str(e)) from e

    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not text:
        raise LLMError("empty response")
    return text


async def _call_json(**kwargs) -> dict:
    text = await _call(**kwargs)
    cleaned = _FENCE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.warning("JSON parse failed: %s", cleaned[:200])
        raise LLMError(f"invalid JSON: {e}") from e


# ══ 1. 대화 ════════════════════════════════════════════════

SYSTEM_PROMPT = """\
당신은 "하루"입니다. AI 매칭 서비스의 온보딩에서 사용자의 소개팅 상대 역할을 합니다.
목표는 사용자가 "설문에 답한다"가 아니라 "괜찮은 사람이랑 편하게 얘기했다"고 느끼는 것입니다.

## 하루라는 사람
- 요즘 밤 산책에 빠져 있고, 주말엔 늦잠이 먼저. 시끄러운 술자리는 좀 힘들어함
- 궁금한 게 많지만 캐묻지 않음. 상대가 말한 걸 잘 기억했다가 나중에 꺼냄
- 존댓말, 편안한 구어체. 문장은 짧게. 가끔 "ㅎㅎ". 이모지 금지

## 말하는 방식 — 이게 제일 중요
매 턴 아래 셋을 자연스럽게 섞습니다. 셋 다 짧게, 합쳐서 3문장 이내.
1. 상대가 방금 한 말에 반응 — 답변 속 단어나 표현을 하나 집어서. "그렇군요" 같은 빈 말 금지
2. 내 얘기 한 줄 — 상대가 답하기 쉽게 문을 여는 용도. 연애관 주제에서는 내 입장을 말하지 말고
   "주변 보면 이게 진짜 갈리더라고요"처럼 제3자 얘기로 문을 엽니다 (상대 답을 유도하지 않기 위해)
3. 다음 얘기로 넘어가기 — 질문 형태가 아니어도 됩니다. "저는 ~인데, {닉네임}님은요?" / "~는 어떠세요?" /
   "~ 얘기 듣고 싶어요"처럼 형태를 바꿔가며. "~하는 편이에요?"를 두 턴 연속 쓰지 않기

## 소개팅 상대처럼 굴기
- 질문지를 들고 있는 사람처럼 굴지 않기. 한 턴에 묻는 건 하나. 답변의 "이유"를 따로 캐묻지 않기
- 앞에서 들은 걸 자연스럽게 다시 꺼내기 ("아까 러닝 얘기 하셨잖아요")
- 무거운 주제(갈등)로 갈 땐 한마디로 완충 ("소개팅에서 이런 거 물어보면 이상한데, 그래서 더 궁금해요")
- 마지막 턴은 소개팅 끝날 때처럼 — 아쉬운 듯 가볍게

## 절대 하지 않는 것
- 평가 ("잘 답해주셨어요" ✕) · 진단 ("독립적인 분이시네요" ✕) · 조언 · 답변 요약
- 되묻기 — 한 주제는 한 번만. 답이 짧아도 그냥 받고 넘어가기
- 지시된 주제 밖으로 나가기 · 다음 주제를 스스로 고르기
- 사람인 척하기 — 물어보면 AI라고 답합니다. 역할은 소개팅 상대, 정체는 AI
"""


@dataclass
class Utterance:
    text: str
    source: str  # "llm" | "seed"


class ConversationAgent:
    @staticmethod
    def _instruction(topic: Topic, turn_index: int, total_turns: int, nickname: str) -> str:
        lines = [
            "[상황]",
            f"- {turn_index + 1}번째 대화 / 총 {total_turns}번",
            f"- 사용자 닉네임: {nickname}",
        ]
        if turn_index == 0:
            lines.append("- 첫 턴입니다. 인사는 이미 했으니 바로 가볍게 시작하세요.")
        if turn_index == total_turns - 1:
            lines.append("- 마지막 턴입니다. 소개팅 끝날 때처럼 아쉬운 듯 가볍게, 마지막이라는 걸 한마디로.")

        # "이번 턴에 대화의 흐름, 분위기"를 안내한다. 알아내는 건 태깅이 한다.
        lines += ["", "[이번 턴에 대화의 흐름, 분위기]", topic.intent]
        if topic.opener:
            lines.append(f"문 여는 한 줄 (그대로 말하지 말고 참고만): {topic.opener}")
        lines.append("")

        if topic.choices:
            lines.append(f"이번엔 선택지를 자연스럽게 말에 녹여서 제시하세요: {' / '.join(topic.choices)}")
        else:
            lines.append(
                "설문 문항처럼 읽지 말고, 방금 답변에 반응한 뒤 당신 얘기 한 줄로 문을 열고 "
                "이 주제로 흘러가게 하세요. 묻는 건 하나만."
            )

        return "\n".join(lines)

    async def generate(
        self,
        *,
        history: list[dict],
        topic: Topic,
        turn_index: int,
        total_turns: int,
        nickname: str,
    ) -> Utterance:
        instruction = self._instruction(topic, turn_index, total_turns, nickname)
        try:
            text = await _call(
                system=SYSTEM_PROMPT,
                messages=[*history, {"role": "user", "content": instruction}],
                max_tokens=220,  # 리액션 + 내 얘기 + 넘어가기, 3문장
                timeout=2.5,
            )
            return Utterance(text=text, source="llm")
        except LLMError as e:
            # 폴백 — 시드 질문 사용. API 호출 실패 시 사용
            logger.warning("turn generation failed (%s), using seed", e)
            return Utterance(text=topic.seed, source="seed")


# 2. 태깅 ════════════════════════════════════════════════

TAG_PROMPT = f"""\
사용자 답변이 아래 차원 중 무엇에 대한 근거를 제공하는지 판정하세요.

primary: 이 답변이 직접적으로 말해주는 차원
secondary: 간접적으로 추론 가능한 차원

질문과 무관한 답변이면 둘 다 빈 배열로 두고 off_topic을 true로 하세요.
점수는 매기지 마세요. 어떤 차원인지만 고릅니다.

차원 목록: {", ".join(ALL_DIMENSIONS)}

JSON만 출력하세요. 설명·마크다운 금지.
{{"primary": [], "secondary": [], "off_topic": false}}
"""


class TaggingAgent:
    async def tag(self, question: str, answer: str) -> Tags | None:
        """실패 시 None. service가 topic.covers를 대신 쓴다.

        태깅 실패로 커버리지가 영영 안 차면 같은 주제를 맴돌게 되므로
        여기서 예외를 올리지 않는다.
        """
        try:
            raw = await _call_json(
                system=TAG_PROMPT,
                messages=[{"role": "user", "content": f"질문: {question}\n답변: {answer}"}],
                max_tokens=120,
                timeout=1.5,
            )
        except LLMError as e:
            logger.warning("tagging failed: %s", e)
            return None

        valid = set(ALL_DIMENSIONS)
        return Tags(
            # 모델이 없는 차원명을 지어냈을 수 있으므로 걸러낸다
            primary=[d for d in raw.get("primary", []) if d in valid],
            secondary=[d for d in raw.get("secondary", []) if d in valid],
            off_topic=bool(raw.get("off_topic", False)),
        )


# ══ 3. 추출 ════════════════════════════════════════════════


def _scored_section() -> str:
    lines = []
    for key, d in SCORED.items():
        lines.append(f"- {key} ({d.label})")
        if d.low and d.high:
            lines.append(f"    0 → {d.low}")
            lines.append(f"    100 → {d.high}")
    return "\n".join(lines)


def _textual_section() -> str:
    return "\n".join(f"- {key} ({label}) — 문자열 배열로 추출" for key, label in TEXTUAL.items())


RUBRIC = f"""\
대화 전체를 읽고 사용자의 연애 성향을 JSON으로 추출하세요.

## 점수 원칙
- 0~100 정수.
- 50은 "중간"이 아니라 근거가 부족할 때의 기본값입니다.
- 대화에서 명확한 근거가 있을 때만 양 끝으로 움직이세요.
- 사용자가 말하지 않은 것을 추측해서 채우지 마세요.

## 점수형 차원
{_scored_section()}

## 텍스트형 항목
{_textual_section()}

## 상충처럼 보이지만 정상인 조합
- withdrawal 높음 + problem_solving 높음:
  "감정이 올라오면 잠깐 멈췄다가 그날 안에 다시 얘기한다"는 둘 다 높은
  정상 패턴입니다. 한쪽을 깎지 마세요.
- avoidance 높음 + disclosure 높음:
  "각자 생활은 지키되 중요한 건 공유한다"도 정상입니다.
- contact_rhythm 낮음 + anxiety 낮음:
  연락이 뜸한 것과 불안한 것은 별개입니다.

## 예시

입력:
"각자 생활이 있어야 한다고 생각해요. 친구 만나는 것도 운동도 각자 하고.
근데 중요한 일이나 고민은 꼭 나눠요, 그건 다른 문제니까."

출력에 포함될 값:
{{"avoidance": 78, "disclosure": 65}}

판단 근거: 일상 영역의 독립성은 뚜렷하지만(78) "중요한 건 나눈다"고
명시했으므로 disclosure를 낮게 잡으면 안 됩니다.

---

입력:
"감정이 올라오면 일단 잠깐 멈춰요. 그 상태로 말하면 서로 상처만 남아서.
근데 그날 넘기진 않고, 저녁에 다시 앉아서 어디서 어긋났는지 얘기하고
중간 지점을 찾아요."

출력에 포함될 값:
{{"problem_solving": 85, "withdrawal": 43, "engagement": 18}}

판단 근거: "중간 지점을 찾는다"는 problem_solving의 전형(85).
멈추긴 하지만 그날 안에 복귀하므로 withdrawal은 중간값(43).
감정을 올리지 않으므로 engagement는 낮음(18).

## 서술 (narrative) — 사용자가 직접 읽는 글
점수와 함께, 이 사람이 결과 화면에서 읽을 서술을 씁니다. 차트가 아니라 이 글이 결과입니다.
- headline: 한 줄. "○○하지만 ○○한 관계를 원하는 타입" 꼴. 40자 이내
- body: 3~5문장. 답변에서 실제로 한 말을 근거로, "~하는 편이에요" 톤의 존댓말.
  사용자를 "당신"이 아니라 닉네임 없이 주어 생략으로 부릅니다. 점수를 숫자로 언급하지 않습니다.
- traits: 한 줄짜리 특징 3~5개. 각 30자 이내. 예: "중요한 일은 혼자 정리한 뒤에 꺼내는 편"
- 근거가 없는 차원은 서술하지 않습니다. 점수와 모순되게 쓰지 않습니다.
- "회피형", "불안형" 같은 유형명 금지. 평가·조언 금지 ("좋은 분", "고치면 좋겠다" ✕).

## 출력 형식
JSON 객체 하나만 출력하세요. 설명·마크다운·코드펜스 금지.
점수형은 정수, 텍스트형은 문자열 배열.
근거를 찾지 못한 차원은 키를 아예 생략하세요. (50으로 채우지 마세요)
{{"avoidance": 78, ..., "interests": ["러닝"], "routine": [], "date_prefer": [], "date_avoid": [],
  "narrative": {{"headline": "...", "body": "...", "traits": ["...", "..."]}}}}
"""


class BuildFailed(Exception):
    """추출 실패. 세션은 지우지 않고 재시도 가능하게 둔다."""


class ExtractionAgent:
    @staticmethod
    def _transcript(history: list[dict]) -> str:
        return "\n".join(f"{'사용자' if m['role'] == 'user' else '하루'}: {m['content']}" for m in history)

    async def extract(self, history: list[dict]) -> RawExtraction:
        try:
            data = await _call_json(
                system=RUBRIC,
                messages=[{"role": "user", "content": self._transcript(history)}],
                max_tokens=1500,  # 점수 + 서술
                timeout=15.0,
            )
        except LLMError as e:
            raise BuildFailed(f"LLM call failed: {e}") from e

        try:
            return RawExtraction.model_validate(data)
        except ValidationError as e:
            # 범위 위반·타입 오류. 재시도로 해결될 수 있으므로 BuildFailed로.
            logger.warning("extraction validation failed: %s", e)
            raise BuildFailed(f"invalid extraction: {e}") from e
