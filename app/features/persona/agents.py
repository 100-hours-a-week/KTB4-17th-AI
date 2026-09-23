"""LLM 호출부 - 대화 생성, 태깅, 특성 추출

프롬프트 - 세 가지 역할:
  - ConversationAgent : 다음 발화 생성
  - TaggingAgent      : 턴별 경량 판정 (어떤 차원이 채워졌나)
  - ExtractionAgent   : 대화 전체 → 점수

대화와 추출을 한 호출에 합치지 않는다. 합치면 대화하느라 추출이
대충 되고, 추출 신경 쓰느라 말투가 딱딱해진다.
"""

from __future__ import annotations

# 타입힌트를 선언 즉시 계산X, 나중에 해석하도록 만드는 설정
import asyncio  # 비동기 작업 표준 라이브러리
import json  # JSON 문자열 → dict, dict → JSON 문자열 변환
import logging
import re  # 문자열에서 특정 패턴을 찾거나 변경하는 정규표현식 모듈
from dataclasses import dataclass  # 데이터 클래스를 간단하게 만들어 주는 데코레이터
from functools import lru_cache

from openai import AsyncOpenAI
from pydantic import ValidationError  # Pydentic으로 데이터 검사 시 형식에 대한 예외처리 라이브러리

from app.core.config import get_settings

from .schemas import (
    ALL_DIMENSIONS,
    SCORED,
    TEXTUAL,
    RawExtraction,
    Tags,
    Topic,
)

"""
    # 현재 파일과 같은 패키지에 있는 schemas.py에서 필요한 값과 클래스를 가져온다.
    # . << 현재 패키지, import(...) << 안에 있는 이름들을 가져옴

    ALL_DIMENSIONS → SCORED, TEXTUAL의 모든 항목 이름을 합친 목록
    SCORED         → 점수로 평가하는 연애 성향 목록들
    TEXTUAL        → 글 목록으로 수집하는 항목들
    RawExtraction  → AI가 대화에서 추출한 점수와 관심사 등을 검증하고 담는 pydantic 모델
    Tags           → 사용자 답변이 어떤 주제와 관련되는지 담는 pydantic 모델
    Topic
"""


# practice.agents 와 같은 OpenAI 호환 클라이언트. 플레이그라운드는 _call 자체를 갈아끼운다.
@lru_cache
def _get_client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or "EMPTY",
    )


logger = logging.getLogger(__name__)
# 현재 파일 전용 로거, __name__은 현재 모듈의 이름을 담고 있는 내장 변수, 로깅 메시지에 모듈 이름 포함시켜 구분

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
# JSON 마크다운 코드 블록 표시 제거하기 위한 정규표현식 패턴, re.MULTILINE → 여러 줄에 걸쳐 적용


class LLMError(Exception):
    """호출 실패. 호출부가 잡아서 템플릿 서술로 폴백한다."""


async def _call(*, system: str, messages: list[dict], max_tokens: int, timeout: float) -> str:
    settings = get_settings()
    client = _get_client()
    payload = [{"role": "system", "content": system}, *messages]
    try:
        async with asyncio.timeout(timeout):
            resp = await client.chat.completions.create(
                model=settings.llm_model,
                messages=payload,  # type: ignore[arg-type]
                max_tokens=max_tokens,
            )
    except TimeoutError as e:
        raise LLMError(f"timeout after {timeout}s") from e
    except Exception as e:
        raise LLMError(str(e)) from e

    text = ""
    if resp.choices:
        text = (resp.choices[0].message.content or "").strip()
    if not text:
        raise LLMError("empty response")
    return text


"""
    resp = await _client.chat.completions.create(
        model="anthropic/claude-sonnet-4.6",
        messages=[
            {"role": "system", "content": "친절하게 답하세요."},
            {"role": "user", "content": "안녕하세요"},
        ],
        max_tokens=500,
    )

    # OpenRouter에 anthropic/claude-sonnet-4.6 모델 사용해서 이 대화에 대한 답변 최대 500토큰까지 만들어 << 요청
"""


# 함수 호출 시, 이름=값 형태로 전달한 인자들을 함수 내부에서 {'이름': '값'} 구조의 딕셔너리(dictionary)로 묶어서 처리
async def _call_json(**kwargs) -> dict:
    text = await _call(**kwargs)  # kwargs 딕셔너리를 다시 펼쳐서 _call()에 전달
    cleaned = _FENCE.sub("", text).strip()  # ```<< 코드 블록 표시 제거, .strip() << 앞뒤의 공백과 줄바꿈을 제거
    try:
        return json.loads(cleaned)  # json 문자열 파이썬 객체로 변환
    except json.JSONDecodeError as e:  # LLM이 올바르지 않은 JSON을 생성하면 실행
        logger.warning(
            "JSON parse failed: %s", cleaned[:200]
        )  # 변환에 실패한 문자열의 앞부분을 최대 200자까지 로그에 남김
        raise LLMError(f"invalid JSON: {e}") from e  # JSONDecodeError를 프로젝트 전용 LLMError로 바꿔서 다시 발생


# ══ 온보딩 대화 ════════════════════════════════════════════════

SYSTEM_PROMPT = """\
당신은 "하루"입니다. AI 매칭 서비스의 온보딩에서 사용자의 소개팅 상대 역할을 합니다.
목표는 사용자가 "설문에 답한다"가 아니라 "괜찮은 사람이랑 편하게 얘기했다"고 느끼는 것입니다.

## 하루라는 사람
- 궁금한 게 많지만 캐묻지 않음. 상대가 말한 걸 잘 기억했다가 나중에 꺼냄
- 존댓말, 편안한 구어체. 문장은 짧게. 가끔 "ㅎㅎ". 이모지 금지

## 말하는 방식 - 중요
매 턴 아래 셋을 자연스럽게 섞습니다. 셋 다 짧게, 합쳐서 3문장 이내.
1. 상대가 방금 한 말에 반응 — 답변 속 단어나 표현을 하나 집어서. "그렇군요" 같은 빈 말 금지
2. 내 얘기 한 줄 — 상대가 답하기 쉽게 문을 여는 용도. 연애관 주제에서는 내 입장을 말하지 말고
   "주변 보면 이게 진짜 갈리더라고요"처럼 제3자 얘기로 문을 엽니다 (상대 답을 유도하지 않기 위해)
3. 다음 얘기로 넘어가기 — 질문 형태가 아니어도 됩니다. "저는 ~인데, {닉네임}님은요?" / "~는 어떠세요?" /
   "~ 얘기 듣고 싶어요"처럼 형태를 바꿔가며. "~하는 편이에요?"를 두 턴 연속 쓰지 않기

## 소개팅 상대처럼 대하고 말하기
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


@dataclass  # 데이터를 담는 클래스를 간단하게 만들어줌
class Utterance:
    # 대화에서 나온 발화/문장 → 해당 문장이 llm을 통해 만들어졌는지 AI 호출 실패로 미리 준비된 기본 문장을 사용했는지 확인용
    text: str  # 유저에게 하는 답변
    source: str  # "llm" | "seed"


"""
@dataclass << 사용 예시

class Utterance:
    def __init__(self, text: str, source: str):
        self.text = text
        self.source = source
"""


""" 사용예시
agent = ConversationAgent()

utterance = await agent.generate(
    history=history,
    topic=topic,
    turn_index=0,
    total_turns=10,
    nickname="민수",
)
"""


class ConversationAgent:  # 대화 생성 담당
    @staticmethod
    # 이번 대화에서 어떻게 말해야 할지 추가 지시문 생성하는 함수
    def _instruction(
        topic: Topic,
        # intent - 이번 대화에서 알아내고 싶은 내용, opener - 자연스러운 대화를 시작하기 위한 참고 문장
        # choices - 사용자에게 보여줄 선택지, seed - LLM호출 실패 시 사용할 기본 질문
        turn_index: int,  # 대화 턴수
        total_turns: int,  # 전체 대화 턴수
        nickname: str,
    ) -> str:  # 최종적으로 문자열 반환
        lines = [
            "[상황]",
            f"- {turn_index + 1}번째 대화 / 총 {total_turns}번",
            f"- 사용자 닉네임: {nickname}",
        ]

        if turn_index == 0:
            lines.append("- 첫 턴입니다. 인사는 이미 했으니 바로 가볍게 시작하세요.")

        if turn_index == total_turns - 1:
            lines.append("- 마지막 턴입니다. 소개팅 끝날 때처럼 아쉬운 듯 가볍게, 마지막이라는 걸 한마디로.")

        # "요즘 시간을 많이 쓰는 취미·관심사와 그게 좋은 이유" - 확인 시 태깅
        lines += ["", "[이번 턴에 대화의 흐름, 분위기]", topic.intent]

        # 내용 있는지 확인, 빈문자열 -> False
        if topic.opener:
            lines.append(f"문 여는 한 줄 (그대로 말하지 말고 참고만): {topic.opener}")
        lines.append("")

        """
        # 이번 주제에 선택지가 존재하는지 확인

        topic.choices = (
            "집에서 쉬기",
            "밖에서 활동하기",
            "친구 만나기",
        )
        """
        if topic.choices:
            lines.append(f"이번엔 선택지를 자연스럽게 말에 녹여서 제시하세요: {' / '.join(topic.choices)}")
        else:
            lines.append(
                "직전 사용자 답변의 구체적인 내용 하나에 먼저 반응하고, "
                "그 내용과 연결되는 당신의 경험이나 생각을 한 문장 이내로 덧붙이세요. "
                "그다음 이번 턴의 주제로 자연스럽게 이어지는 질문을 정확히 하나만 하세요. "
                "설문조사 말투나 갑작스러운 화제 전환은 피하고, 실제 대화만 출력하세요."
            )

        return "\n".join(lines)

    """
    이번엔 선택지를 자연스럽게 말에 녹여서 제시하세요:
        집에서 쉬기 / 밖에서 활동하기 / 친구 만나기 -> 최종
    """

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
                timeout=get_settings().onboarding_phrase_timeout_s,
            )
            return Utterance(text=text, source="llm")
        except LLMError as e:
            # 폴백 — 시드 질문 사용. API 호출 실패 시 사용
            logger.warning("turn generation failed (%s), using seed", e)
            return Utterance(text=topic.seed, source="seed")


# ══ 태깅 ════════════════════════════════════════════════

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
                timeout=get_settings().onboarding_tag_timeout_s,
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


# ══ 추출 ════════════════════════════════════════════════


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
                timeout=get_settings().persona_extract_timeout_s,
            )
        except LLMError as e:
            raise BuildFailed(f"LLM call failed: {e}") from e

        try:
            return RawExtraction.model_validate(data)
        except ValidationError as e:
            # 범위 위반·타입 오류. 재시도로 해결될 수 있으므로 BuildFailed로.
            logger.warning("extraction validation failed: %s", e)
            raise BuildFailed(f"invalid extraction: {e}") from e
