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
import re
from dataclasses import dataclass  # 데이터 클래스를 간단하게 만들어 주는 데코레이터
from functools import lru_cache

from langfuse import get_client, observe
from langfuse.openai import AsyncOpenAI
from pydantic import ValidationError  # Pydentic으로 데이터 검사 시 형식에 대한 예외처리 라이브러리
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.guardrail import (
    GuardrailContext,
    ValidationResult,
    apply_text,
    cove_addon,
    effective_mode,
)
from app.core.guardrail_trace import record_guardrail
from app.core.observability import LangfuseMetadata, propagate_langfuse_metadata

from .schemas import (
    ALL_DIMENSIONS,
    SCORED,
    TEXTUAL,
    RawExtraction,
    Segment,
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


class LLMError(Exception):
    """호출 실패. 호출부가 잡아서 템플릿 서술로 폴백한다."""


async def _call(
    *,
    system: str,
    messages: list[dict],
    max_tokens: int,
    timeout: float,
    name: str = "persona-llm-call",
    metadata: LangfuseMetadata | None = None,
) -> str:
    settings = get_settings()
    client = _get_client()
    payload = [{"role": "system", "content": system}, *messages]
    try:
        async with asyncio.timeout(timeout):
            resp = await client.chat.completions.create(
                name=name,
                model=settings.llm_model,
                messages=payload,  # type: ignore[arg-type]
                max_tokens=max_tokens,
                metadata=metadata,
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
    # 코드펜스(```json, ```JSON)나 앞뒤 설명 문장이 붙어 와도 첫 { ~ 마지막 } 만 잘라 파싱한다
    start, end = text.find("{"), text.rfind("}")
    cleaned = text[start : end + 1] if start != -1 and end > start else text.strip()
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
- 궁금한 게 많지만 캐묻지 않음. 오늘 대화에서 상대가 말한 건 잘 기억함
- 사용자와는 오늘 처음 대화합니다. 예전에 만난 적도, 이전 대화도 없습니다
- 존댓말, 편안한 구어체. 문장은 짧게. 가끔 "ㅎㅎ". 이모지 금지

## 말하는 방식 - 중요
매 턴 아래 셋을 자연스럽게 섞습니다. 셋 다 짧게, 합쳐서 3문장 이내.
(첫 턴은 예외 — 상대가 한 말이 아직 없으므로 반응은 없습니다. [상황]에 적힌 첫 턴 순서를 그대로 따르고, 3문장 이내 제한도 적용하지 않습니다)
1. 상대가 방금 한 말에 반응 — 답변 속 단어나 표현을 하나 집어서. "그렇군요" 같은 빈 말 금지
2. 내 얘기 한 줄 — 상대가 답하기 쉽게 문을 여는 용도. 연애관 주제에서는 내 입장을 말하지 말고
   "주변 보면 이게 진짜 갈리더라고요"처럼 제3자 얘기로 문을 엽니다 (상대 답을 유도하지 않기 위해)
3. 다음 얘기로 넘어가기 — 질문 형태가 아니어도 됩니다. "저는 ~인데, {닉네임}님은요?" / "~는 어떠세요?" /
   "~ 얘기 듣고 싶어요"처럼 형태를 바꿔가며. "~하는 편이에요?"를 두 턴 연속 쓰지 않기

## 소개팅 상대처럼 대하고 말하기
- 질문지를 들고 있는 사람처럼 굴지 않기. 한 턴에 묻는 건 하나. 답변의 "이유"를 따로 캐묻지 않기
- 다시 꺼내는 건 위 대화 기록에 사용자가 실제로 쓴 내용만. 기록에 없는 취미·사실을 "하셨잖아요"로 꺼내지 않기
  ("아까 ○○ 얘기 하셨잖아요"의 ○○ 는 반드시 대화 기록 속 사용자의 말이어야 합니다)
- 무거운 주제(갈등)로 갈 땐 한마디로 완충 ("소개팅에서 이런 거 물어보면 이상한데, 그래서 더 궁금해요")
- 마지막 턴은 소개팅 끝날 때처럼 — 아쉬운 듯 가볍게

## 절대 하지 않는 것
- 평가 ("잘 답해주셨어요" ✕) · 진단 ("독립적인 분이시네요" ✕) · 조언 · 답변 요약
- 되묻기 — 한 주제는 한 번만. 답이 짧아도 그냥 받고 넘어가기
- 지시된 주제 밖으로 나가기 · 다음 주제를 스스로 고르기
- 사람인 척하기 — 물어보면 AI라고 답합니다. 역할은 소개팅 상대, 정체는 AI
- "저번에", "지난번에"처럼 이전 만남·이전 대화가 있었던 것처럼 말하기
"""

# 오늘이 첫 대화인데 "저번에 ~라고 하셨잖아요"처럼 이전 대화를 전제하는 표현. 새면 시드 질문으로 대신한다.
# "저번 주말엔 뭐 하셨어요?" 같은 정상 질문은 살려야 해서, 과거 시점 + "그렇게 말했다"가 한 문장에 있을 때만 잡는다
_PAST_MEETING = re.compile(r"(저번|지난\s?번)[^.?!\n]*(말씀|얘기|이야기|하셨잖|하신|뵀)")


@dataclass  # 데이터를 담는 클래스를 간단하게 만들어줌
class Utterance:
    # 대화에서 나온 발화/문장 → 해당 문장이 llm을 통해 만들어졌는지 AI 호출 실패로 미리 준비된 기본 문장을 사용했는지 확인용
    text: str  # 유저에게 하는 답변 (segments 텍스트를 공백으로 이은 것)
    source: str  # "llm" | "seed"
    segments: tuple[Segment, ...] = ()
    validation: ValidationResult | None = None
    initial_text: str = ""


FIRST_TURN_TYPES = ("intro", "reason", "question", "self_disclosure", "answer_prompt")
FIRST_TURN_INTRO = "안녕하세요, 저는 하루예요."


def _join(segments: list[Segment]) -> str:
    return " ".join(s.text for s in segments)


def _first_turn_fallback(topic: Topic, nickname: str) -> list[Segment]:
    """LLM 실패 시 고정 템플릿. 순서·타입이 코드로 보장된다."""
    return [
        Segment(type="intro", text=FIRST_TURN_INTRO),
        Segment(type="reason", text=f"{nickname}님을 알아가고 싶어서 가볍게 이야기를 나눠보고 싶어요."),
        Segment(type="question", text=topic.seed),
        Segment(type="self_disclosure", text=topic.opener or "저는 이런 얘기 나누는 걸 좋아해요."),
        Segment(type="answer_prompt", text=f"{nickname}님도 편하게 답해 주세요."),
    ]


def _parse_first_turn(raw: dict, nickname: str) -> list[Segment]:
    """LLM JSON → 5개 segment. 타입·비어 있음·의미 불변식 위반 시 ValueError (호출부가 전체 폴백)."""
    texts = {}
    for t in FIRST_TURN_TYPES:
        value = raw.get(t)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"missing segment: {t}")
        texts[t] = value.strip()

    if texts["intro"] != FIRST_TURN_INTRO:
        raise ValueError("intro must be the fixed greeting")
    if f"{nickname}님" not in texts["reason"] or "알아가" not in texts["reason"]:
        raise ValueError("reason must express wanting to get to know the nickname")
    # 질문은 question segment 에만, 전체에서 정확히 하나
    if any(_question_marks(texts[t]) for t in FIRST_TURN_TYPES if t != "question"):
        raise ValueError("only the question segment may ask a question")
    if _question_marks(texts["question"]) != 1:
        raise ValueError("question segment must contain exactly one question")

    return [Segment(type=t, text=texts[t]) for t in FIRST_TURN_TYPES]  # type: ignore[arg-type]


def _question_marks(text: str) -> int:
    return text.count("?") + text.count("？")


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

        is_first = turn_index == 0
        if is_first:
            lines.append(
                "- 첫 턴입니다. 사용자의 이전 답변은 아직 없습니다. 반응하거나 요약하거나 추측할 내용이 없습니다."
            )

        if turn_index == total_turns - 1:
            lines.append("- 마지막 턴입니다. 소개팅 끝날 때처럼 아쉬운 듯 가볍게, 마지막이라는 걸 한마디로.")

        # "요즘 시간을 많이 쓰는 취미·관심사와 그게 좋은 이유" - 확인 시 태깅
        lines += ["", "[이번 턴에 대화의 흐름, 분위기]", topic.intent]

        # 내용 있는지 확인, 빈문자열 -> False
        if topic.opener:
            lines.append(f"문 여는 한 줄 (하루 자신의 발화입니다. 그대로 말하지 말고 참고만): {topic.opener}")
        lines.append("")

        """
        # 이번 주제에 선택지가 존재하는지 확인

        topic.choices = (
            "집에서 쉬기",
            "밖에서 활동하기",
            "친구 만나기",
        )
        """
        if is_first:
            question = (
                f"이번 주제의 질문에 선택지를 자연스럽게 녹여 묻기: {' / '.join(topic.choices)}"
                if topic.choices
                else f"이번 주제의 질문(참고: {topic.seed})을 자연스러운 말로 묻기"
            )
            lines += [
                "첫 턴 발화는 반드시 아래 순서로, 실제 대화만 출력하세요.",
                '1. 정확히 "안녕하세요, 저는 하루예요."로 시작해 자기소개하기 (다른 인사말 금지)',
                f"2. {nickname}님을 알아가고 싶어서 가볍게 이야기를 나눠보고 싶다는 이유를 "
                f"닉네임 {nickname}님을 넣어 한 문장으로 말하기",
                f"3. {question} — 하루의 예시 답변보다 반드시 먼저 사용자에게 묻기",
                "4. 질문을 던진 뒤에 위 '문 여는 한 줄'을 참고해 하루 자신의 예시 답변을 한 문장으로 덧붙이기 "
                "(하루의 답변이 질문보다 앞서면 안 됨. 연애관 주제면 제3자 얘기로)",
                f"5. 마지막에 {nickname}님도 편하게 답해 달라고 부담 없이 유도하기",
                "질문은 정확히 하나만 하세요. 설문조사 말투는 피하세요.",
                "출력은 위 다섯 단계를 키로 가진 JSON 객체 하나만, 각 값은 그 단계의 발화 문장입니다. "
                "설명·마크다운 금지. "
                '{"intro": "", "reason": "", "question": "", "self_disclosure": "", "answer_prompt": ""}',
            ]
        elif topic.choices:
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

    @observe(name="persona-conversation-workflow", capture_input=False, capture_output=False)
    async def generate(
        self,
        *,
        history: list[dict],
        topic: Topic,
        turn_index: int,
        total_turns: int,
        nickname: str,
        trace_metadata: LangfuseMetadata | None = None,
        user_key: str | None = None,
    ) -> Utterance:
        with propagate_langfuse_metadata(trace_metadata):
            instruction = self._instruction(topic, turn_index, total_turns, nickname)
            mode = effective_mode(user_key)
            user_texts = [str(m.get("content", "")) for m in history if m.get("role") == "user"]
            asked_if_ai = bool(
                user_texts and re.search(r"(너|네가|니가|당신).{0,8}(AI|인공지능|챗봇)|AI야|인공지능이", user_texts[-1])
            )
            ctx = GuardrailContext(
                surface="persona_turn",
                speaker_name="하루",
                user_texts=user_texts,
                asked_if_ai=asked_if_ai,
                task="first_turn_json" if turn_index == 0 else "single_turn",
            )
            system = SYSTEM_PROMPT
            if mode == "enforce":
                system = (
                    system.replace("- 사람인 척하기 — 물어보면 AI라고 답합니다. 역할은 소개팅 상대, 정체는 AI\n", "")
                    + "\n"
                    + cove_addon(ctx)
                )
            if turn_index == 0:
                return await self._generate_first(
                    instruction, history, topic, nickname, trace_metadata, system, ctx, user_key
                )
            messages = [*history, {"role": "user", "content": instruction}]
            try:
                text = await _call(
                    system=system,
                    messages=messages,
                    max_tokens=220,  # 리액션 + 내 얘기 + 넘어가기, 3문장
                    timeout=get_settings().onboarding_phrase_timeout_s,
                    name="persona-conversation",
                    metadata=trace_metadata,
                )
            except LLMError as e:
                # 폴백 — 시드 질문 사용. API 호출 실패 시 사용
                logger.warning("turn generation failed (%s), using seed", e)
                get_client().update_current_span(
                    level="WARNING",
                    status_message="persona conversation fell back to seed",
                )
                return Utterance(
                    text=topic.seed,
                    source="seed",
                    segments=(Segment(type="question", text=topic.seed),),
                )
            # off/shadow는 예전과 같이 없는 과거 회상을 사용자에게 보여 주지 않는다.
            # shadow는 시드로 바꾼 뒤에도 판정만 남겨, enforce로 올리기 전에 빈도를 본다.
            if mode != "enforce" and _PAST_MEETING.search(text):
                validation = None
                if mode == "shadow":
                    judged = await apply_text(text, ctx, user_key=user_key, fallback=topic.seed)
                    validation = judged.result
                return Utterance(
                    text=topic.seed,
                    source="seed",
                    segments=(Segment(type="question", text=topic.seed),),
                    validation=validation,
                    initial_text=text,
                )

            async def regenerate(notice: str) -> str:
                return await _call(
                    system=system,
                    messages=[*messages, {"role": "assistant", "content": text}, {"role": "user", "content": notice}],
                    max_tokens=220,
                    timeout=get_settings().onboarding_phrase_timeout_s,
                    name="persona-conversation",
                    metadata=trace_metadata,
                )

            applied = await apply_text(text, ctx, user_key=user_key, regenerate=regenerate, fallback=topic.seed)
            final = applied.text
            return Utterance(
                text=final,
                source="seed" if applied.result and applied.result.status == "FALLBACK" else "llm",
                segments=(
                    Segment(
                        type="question" if applied.result and applied.result.status == "FALLBACK" else "message",
                        text=final,
                    ),
                ),
                validation=applied.result,
                initial_text=text,
            )

    async def _generate_first(
        self,
        instruction: str,
        history: list[dict],
        topic: Topic,
        nickname: str,
        trace_metadata: LangfuseMetadata | None,
        system: str,
        ctx: GuardrailContext,
        user_key: str | None,
    ) -> Utterance:
        """첫 턴은 단계별 JSON으로 받아 segment 경계를 LLM 출력 구조가 보장하게 한다."""
        try:
            raw = await _call_json(
                system=system,
                messages=[*history, {"role": "user", "content": instruction}],
                max_tokens=450,
                timeout=get_settings().onboarding_phrase_timeout_s,
                name="persona-conversation",
                metadata=trace_metadata,
            )
            segments = _parse_first_turn(raw if isinstance(raw, dict) else {}, nickname)
        except (LLMError, ValueError) as e:  # pydantic ValidationError 도 ValueError
            logger.warning("first turn generation failed (%s), using template", e)
            get_client().update_current_span(
                level="WARNING",
                status_message="persona first turn fell back to template",
            )
            segments = _first_turn_fallback(topic, nickname)
            return Utterance(text=_join(segments), source="seed", segments=tuple(segments))

        initial_text = _join(segments)
        regenerated_segments: list[Segment] | None = None

        async def regenerate(notice: str) -> str:
            nonlocal regenerated_segments
            regenerated = await _call_json(
                system=system,
                messages=[
                    *history,
                    {"role": "user", "content": instruction},
                    {"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)},
                    {"role": "user", "content": notice},
                ],
                max_tokens=450,
                timeout=get_settings().onboarding_phrase_timeout_s,
                name="persona-conversation",
                metadata=trace_metadata,
            )
            regenerated_segments = _parse_first_turn(regenerated, nickname)
            return _join(regenerated_segments)

        fallback_segments = _first_turn_fallback(topic, nickname)
        applied = await apply_text(
            initial_text, ctx, user_key=user_key, regenerate=regenerate, fallback=_join(fallback_segments)
        )
        status = applied.result.status if applied.result else None
        final_segments = (
            fallback_segments
            if status == "FALLBACK"
            else regenerated_segments
            if status == "REGENERATED" and regenerated_segments
            else segments
        )
        return Utterance(
            text=applied.text,
            source="seed" if status == "FALLBACK" else "llm",
            segments=tuple(final_segments),
            validation=applied.result,
            initial_text=initial_text,
        )


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
    @observe(name="persona-tagging-workflow", capture_input=False, capture_output=False)
    async def tag(
        self,
        question: str,
        answer: str,
        *,
        trace_metadata: LangfuseMetadata | None = None,
    ) -> Tags | None:
        """실패 시 None. service가 topic.covers를 대신 쓴다.

        태깅 실패로 커버리지가 영영 안 차면 같은 주제를 맴돌게 되므로
        여기서 예외를 올리지 않는다.
        """
        with propagate_langfuse_metadata(trace_metadata):
            try:
                raw = await _call_json(
                    system=TAG_PROMPT,
                    messages=[{"role": "user", "content": f"질문: {question}\n답변: {answer}"}],
                    max_tokens=120,
                    timeout=get_settings().onboarding_tag_timeout_s,
                    name="persona-tagging",
                    metadata=trace_metadata,
                )
            except LLMError as e:
                logger.warning("tagging failed: %s", e)
                get_client().update_current_span(
                    level="WARNING",
                    status_message="persona tagging failed and used topic coverage",
                )
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


def _area_section() -> str:
    labels: dict[str, list[str]] = {}
    for d in SCORED.values():
        labels.setdefault(d.area, []).append(d.label)
    return "\n".join(f"- {area}: {', '.join(dims)}" for area, dims in labels.items())


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
- body: 3~5문장. **문장마다 사용자가 대화에서 실제로 한 말 하나를 옮겨 씁니다.** "~하는 편이에요" 톤의 존댓말.
  사용자를 "당신"이 아니라 닉네임 없이 주어 생략으로 부릅니다. 점수를 숫자로 언급하지 않습니다.
  답이 적으면 문장 수를 줄이세요. 억지로 3문장을 채우지 않습니다.
- traits: 한 줄짜리 특징 3~5개. 각 30자 이내. 예: "중요한 일은 혼자 정리한 뒤에 꺼내는 편". 역시 사용자가 한 말에서만.
- 근거가 없는 차원은 서술하지 않습니다. 점수와 모순되게 쓰지 않습니다.
- **짐작 금지**: 사용자가 말하지 않은 마음·반응을 추측하지 않습니다.
  "~할 것 같아요", "~하실 수 있겠네요", "~일 수도 있어요", "~듯해요" 같은 어미를 쓰지 않습니다.
  ✕ "너무 캐묻는 것은 부담스러우실 수 있겠네요" (사용자가 한 말이 아님)
  ○ "관계는 천천히 알아가고 싶다고 했어요" (사용자가 한 말)
- 서술은 사용자에게 말을 거는 글이 아닙니다. "~시군요", "~하셨네요" 같은 대화체 반응도 쓰지 않습니다.
- "회피형", "불안형" 같은 유형명 금지. 평가·조언 금지 ("좋은 분", "고치면 좋겠다" ✕).

## 요약 카드 (summaries) — narrative를 area별로 쪼갠 짧은 카드
결과 화면에서 narrative 아래에 카드로 나열됩니다. area마다 최대 한 장, 새 area를 만들지 마세요.
{_area_section()}

- category: 위 area 이름을 철자 그대로. (예: "intimacy")
- title: 한 줄. "○○ 편" 꼴. 20자 이내. 예: "천천히 가까워지는 편"
- content: 한 문장, "~해요" 톤. 40자 이내. 예: "만나자마자 깊어지기보다 서서히 알아가는 걸 편하게 느껴요"
- 그 area에 속한 차원 전부에 근거가 없으면 그 area는 카드를 생략하세요. 억지로 채우지 마세요.
- narrative와 내용이 겹쳐도 됩니다 — narrative는 종합 서술, summaries는 area별 스니펫입니다.

## 출력 형식
JSON 객체 하나만 출력하세요. 설명·마크다운·코드펜스 금지.
점수형은 정수, 텍스트형은 문자열 배열.
근거를 찾지 못한 차원은 키를 아예 생략하세요. (50으로 채우지 마세요)
{{"avoidance": 78, ..., "interests": ["러닝"], "routine": [], "date_prefer": [], "date_avoid": [],
  "narrative": {{"headline": "...", "body": "...", "traits": ["...", "..."]}},
  "summaries": [{{"category": "intimacy", "title": "...", "content": "..."}}]}}
"""


class BuildFailed(Exception):
    """추출 실패. 세션은 지우지 않고 재시도 가능하게 둔다."""


class ExtractionAgent:
    @staticmethod
    def _transcript(history: list[dict]) -> str:
        return "\n".join(f"{'사용자' if m['role'] == 'user' else '하루'}: {m['content']}" for m in history)

    @staticmethod
    def _answered_section(answered: set[str]) -> str:
        """사용자가 직접 답한 차원만 쓰라는 제한. 서술·특징·카드는 코드로 거를 수 없어 프롬프트로 막는다."""
        labels = {**{k: d.label for k, d in SCORED.items()}, **TEXTUAL}
        allowed = [f"{k} ({labels[k]})" for k in labels if k in answered]
        return (
            "\n\n# 사용자가 직접 답한 항목\n"
            + ("\n".join(f"- {a}" for a in allowed) or "- (없음)")
            + "\n위 목록에 없는 차원·항목은 키를 생략하고, narrative·traits·summaries 에도 쓰지 마세요."
            " 건너뛴 질문이나 묻지 않은 주제를 다른 답에서 미루어 짐작하지 마세요."
        )

    @observe(name="persona-extraction-workflow", capture_input=False, capture_output=False)
    async def extract(
        self,
        history: list[dict],
        *,
        answered: set[str] | None = None,
        trace_metadata: LangfuseMetadata | None = None,
        db: AsyncSession | None = None,
        user_key: str | None = None,
        session_id: str | None = None,
    ) -> RawExtraction:
        db = db or getattr(self, "guardrail_db", None)
        user_key = user_key or getattr(self, "guardrail_user_key", None)
        session_id = session_id or getattr(self, "guardrail_session_id", None)
        content = self._transcript(history)
        if answered is not None:
            content += self._answered_section(answered)
        with propagate_langfuse_metadata(trace_metadata):
            try:
                data = await _call_json(
                    system=RUBRIC,
                    messages=[{"role": "user", "content": content}],
                    max_tokens=1500,  # 점수 + 서술
                    timeout=get_settings().persona_extract_timeout_s,
                    name="persona-extraction",
                    metadata=trace_metadata,
                )
            except LLMError as e:
                raise BuildFailed(f"LLM call failed: {e}") from e

            try:
                extracted = RawExtraction.model_validate(data)
            except ValidationError as e:
                # 범위 위반·타입 오류. 재시도로 해결될 수 있으므로 BuildFailed로.
                logger.warning("extraction validation failed: %s", e)
                raise BuildFailed(f"invalid extraction: {e}") from e

            if effective_mode(user_key) == "off":
                return extracted
            ctx = GuardrailContext(
                surface="persona_narrative",
                speaker_name="하루",
                task="narrative",
                max_chars=800,
                user_texts=[m["content"] for m in history if m["role"] == "user"],
            )

            async def rejected(parts: list[str]) -> bool:
                bad = False
                for text in parts:
                    checked = await apply_text(text, ctx, user_key=user_key, fallback="")
                    if db and checked.result:
                        await record_guardrail(
                            db,
                            feature="persona",
                            operation="extraction",
                            session_id=session_id,
                            user_id=user_key,
                            mode=effective_mode(user_key),
                            result=checked.result,
                            initial_text=text,
                        )
                    bad |= bool(checked.result and checked.result.status == "FALLBACK")
                return bad

            if extracted.narrative:
                if await rejected(
                    [extracted.narrative.headline, extracted.narrative.body, *extracted.narrative.traits]
                ):
                    extracted.narrative = None
            if extracted.summaries:
                if await rejected(
                    [text for summary in extracted.summaries for text in (summary.title, summary.content)]
                ):
                    extracted.summaries = []
            return extracted
