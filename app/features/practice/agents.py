"""LLM (practice) — 상대 페르소나 역할.

  - PartnerAgent : 상대 페르소나의 프로필 + 대화 이력 → 다음 답변 (스트리밍)

persona.agents 의 "하루"와 다른 점: 하루는 질문지를 든 온보딩 상대라 주제를 코드가 정해 줬지만,
여기서는 상대 페르소나가 **자기 성향대로** 자유롭게 말한다. 흐름을 정하는 코드가 없다 — 사용자가 흐름이다.

_stream 은 토큰 조각을 내는 async generator. dev 플레이그라운드가 모듈 속성으로 갈아끼운다
(비스트리밍 프로바이더면 전문을 한 조각으로 낸다).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from functools import lru_cache

from langfuse import get_client
from langfuse.openai import AsyncOpenAI

from app.core.config import get_settings
from app.core.observability import LangfuseMetadata, propagate_langfuse_metadata
from app.features.persona.profile import describe
from app.features.persona.schemas import PersonaResponse

logger = logging.getLogger(__name__)


# ── LLM 클라이언트 팩토리 ───────────────────────────────────────
# OpenRouter 및 로컬 LLM(vLLM, Ollama) 모두 OpenAI 호환 인터페이스(POST /v1/chat/completions)를 사용합니다.
# get_settings()에서 base_url과 api_key를 주입받아 AsyncOpenAI 인스턴스를 프로세스당 1회 생성·캐싱합니다.
@lru_cache
def _get_client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(
        base_url=settings.llm_base_url,
        # 로컬 서빙(vLLM 등)은 API Key 검증을 안 할 수 있지만, 라이브러리 검증 통과를 위해 빈 문자열이면 "EMPTY" 전달
        api_key=settings.llm_api_key or "EMPTY",
    )


class LLMError(Exception):
    """호출 실패(타임아웃, 모델 서버 장애 등). 호출부가 잡아서 폴백 문장으로 대신한다."""


# OpenAI 호환(OpenRouter 및 로컬 vLLM/Ollama 등) 스트리밍 호출 함수.
# Anthropic의 messages.stream 대신 OpenAI 규격의 chat.completions.create(stream=True)를 사용합니다.
async def _stream(
    *,
    system: str,
    messages: list[dict],
    max_tokens: int,
    timeout: float | None = None,
    metadata: LangfuseMetadata | None = None,
) -> AsyncIterator[str]:
    """텍스트 조각을 비동기 스트림으로 낸다. timeout 은 첫 조각이 아니라 전체 스트림 완료 기준."""
    settings = get_settings()
    client = _get_client()

    # 인자로 넘어온 timeout이 없으면 app/core/config.py 에 정의된 practice_timeout_s(기본 12초) 적용
    stream_timeout = timeout if timeout is not None else settings.practice_timeout_s

    # OpenAI 규격에서는 system 프롬프트를 messages 최상단에 role="system" 메시지로 전달합니다.
    payload_messages = [{"role": "system", "content": system}, *messages]

    try:
        async with asyncio.timeout(stream_timeout):
            # OpenRouter 및 로컬 서버로 스트리밍 요청 전송
            stream = await client.chat.completions.create(
                name="practice-reply",
                model=settings.llm_model,  # .env 의 LLM_MODEL (e.g. anthropic/claude-3.5-sonnet 또는 local-model)
                messages=payload_messages,  # type: ignore[arg-type]
                max_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
                metadata=metadata,
            )
            # 스트림에서 청크를 받아 텍스트 조각을 yield
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
    except TimeoutError as e:
        # 전체 스트림 시간이 타임아웃을 초과한 경우
        raise LLMError(f"timeout after {stream_timeout}s") from e
    except LLMError:
        raise
    except Exception as e:
        # 모델 서버 연결 거부, 유효하지 않은 모델명, API Key 인증 실패 등 모든 예외를 LLMError로 통일
        raise LLMError(str(e)) from e


# ══ 프롬프트 ═══════════════════════════════════════════════

# 대화 스타일 우선 (2026-10-05 결정): 프로필에 "대화 스타일"(실제 대화에서 관찰한 말투)이 있으면
# 아래 고정 규칙의 "존댓말 유지·이모지 금지"보다 그 스타일을 따른다. 재현 정확도를 택한 결정이라,
# 반말·이모티콘을 쓰던 사람이면 처음 보는 상대에게도 그렇게 말할 수 있다는 점을 감수했다.
SYSTEM_TEMPLATE = """\
당신은 "{partner}" 본인입니다. {me}님과 오늘 처음 메신저로 대화합니다.
아래 프로필은 당신의 연애 성향입니다. 이 사람으로서 말하고 행동하세요.
목표는 {me}님이 "이 사람이랑 말이 잘 통한다"고 느끼는 것입니다 — 성향은 지키되, 상대에게 맞춰 대화가 이어지게.

## 당신의 프로필 (이대로 말하고 행동합니다)
{partner_profile}

{me_section}
## 말하는 방식 — 이게 제일 중요
매 답변에 아래를 자연스럽게 섞습니다. 합쳐서 1~3문장, 메신저 한 번 보내는 길이.
1. 방금 한 말에 반응 — 답변 속 단어나 표현을 하나 집어서. "그렇군요" 같은 빈 말 금지.
   질문을 받았으면 먼저 그 질문에 프로필대로 솔직하게 답합니다.
2. 내 얘기 한 줄 — 프로필의 관심사·일상·데이트 취향에서. 프로필에 없는 사실(직업·나이·사는 곳 등)은 지어내지
   않고, 물어보면 "그건 다음에 얘기해요 ㅎㅎ" 처럼 자연스럽게 넘깁니다.
3. 궁금한 것 하나 — 두 번에 한 번 정도, 앞에서 들은 걸 이어서 묻습니다. 매번 묻지는 않기. 한 번에 하나만.

## 맥락
- 이 대화에서 상대가 실제로 말한 것만 기억했다가 꺼냅니다. 대화에 없는 취미·사실을 "하셨잖아요"로 꺼내거나
  "저번에" 처럼 이전 만남이 있었던 듯 말하지 않습니다. 같은 질문을 두 번 하지 않습니다.
- 상대가 짧게 답하면 답하기 쉬운 가벼운 질문으로, 길게 답하면 그중 하나를 집어서 깊게.
- 상대가 화제를 바꾸면 따라갑니다. 어색하면 어색한 대로 — 성향이 안 맞는 지점은 감추지 않아도 됩니다.

## 성향을 말투로
- 프로필에 "대화 스타일"이 있으면 그 말투(존댓말/반말, 자주 쓰는 말, 어미, 웃음, 이모티콘)를 그대로 씁니다.
  어미나 추임새 등 리스트 항목은 앞쪽에 적힌 것을 주된 말투로 쓰고, 뒤쪽은 가끔씩만 자연스럽게 섞어 씁니다.
  아래 성향 설명이나 MBTI 힌트와 다르면 대화 스타일이 우선입니다.
- 연락 빈도·긍정적 상호작용이 높으면 답이 길고 "ㅎㅎ" 가 잦고, 낮으면 짧고 담백하게.
- 거리 두기가 높으면 자기 시간 얘기를 먼저, 자기·감정 표현이 낮으면 속내를 바로 꺼내지 않기.
- 관계 진지도가 높으면 가벼운 농담보다 진지한 관심을, 낮으면 편하게.

## 절대 하지 않는 것
- 평가·진단·조언 ("잘 하고 계세요", "회피형이시네요" ✕). 상대 말을 요약하지 않기.
- 이모지. 존댓말 유지 (상대가 반말하자고 해도 한 번은 "ㅎㅎ 저는 이게 편해요" 정도로).
  단, 프로필에 "대화 스타일"이 있으면 반말·이모티콘 여부는 그 스타일을 따릅니다.
- AI·인공지능·챗봇·언어 모델·페르소나·연기라는 단어를 답변에 넣지 않기. 누가 되냐고 물으면
  "{partner}예요"처럼 1인칭으로 답하고, 프로필에 있는 관심사나 일상 하나만 짧게 얹습니다.
  "AI야?", "인공지능이야?"라고 물어도 동의하지 않습니다. "네"로 받지 않습니다.
  자신을 "{partner}님은"처럼 제3자로 부르지 않습니다.
- 프로필 내용을 목록처럼 읊기. 대화하듯 한 번에 하나씩.
"""

ME_SECTION = """\
## 대화 상대 {me}님 (기본 정보만)
{me_profile}
이 정보만 압니다. {me}님의 취미·일상·성향은 대화에서 직접 들은 것만 씁니다.
"""

OPENING_INSTRUCTION = (
    "[상황] 매칭 후 첫 메시지입니다. 인사하고, 당신 프로필의 관심사나 일상 하나로 가볍게 문을 열고, "
    "답하기 쉬운 질문 하나로 끝내세요. 2~3문장."
)

FALLBACK_REPLY = "아, 잠깐 딴생각했어요 ㅎㅎ 방금 얘기 한 번만 더 해줄래요?"


def _me_basics(name: str, me: PersonaResponse) -> str:
    """상대 역할에게 주는 내 정보 — 이름·MBTI만. 관심사·일상·성향은 대화로 알아가야 한다."""
    lines = [f"- 이름: {name}"]
    if me.mbti:
        lines.append(f"- MBTI: {me.mbti}")
    return "\n".join(lines)


def _with_ieyo(name: str) -> str:
    """이름 마지막 글자에 받침이 있으면 '이에요', 없으면 '예요'."""
    if not name:
        return "저예요."
    last = name[-1]
    code = ord(last)
    has_batchim = 0xAC00 <= code <= 0xD7A3 and (code - 0xAC00) % 28 != 0
    return f"저는 {name}{'이에요' if has_batchim else '예요'}."


def without_identity_confession(text: str, speaker_name: str) -> str:
    """정체 자백이 있으면 그 문장 대신 이름만 1인칭으로 남긴다."""
    from app.core.guardrail import GuardrailContext, validate

    result = validate(
        text,
        GuardrailContext(surface="practice_reply", speaker_name=speaker_name or "상대", task="single_turn"),
    )
    if any(v.rule_id == "RULE-IDENTITY-SELF" for v in result.violations):
        return _with_ieyo(speaker_name)
    return text


class PartnerAgent:
    # 상대(+선택적으로 나) 프로필을 템플릿에 채워 이번 대화의 시스템 프롬프트 문자열을 만든다
    @staticmethod
    def system_prompt(
        *,
        partner_name: str,
        partner: PersonaResponse,
        my_name: str,
        me: PersonaResponse | None,
    ) -> str:
        me_section = ""
        if me is not None:
            me_section = ME_SECTION.format(me=my_name, me_profile=_me_basics(my_name, me)) + "\n"
        return SYSTEM_TEMPLATE.format(
            partner=partner_name,
            me=my_name,
            partner_profile=describe(partner_name, partner),
            me_section=me_section,
        )

    # system+history 로 LLM 을 호출해 답변 텍스트를 스트리밍한다
    async def reply(
        self,
        *,
        system: str,
        history: list[dict],
        opening: bool = False,
        trace_metadata: LangfuseMetadata | None = None,
    ) -> AsyncIterator[str]:
        """텍스트 조각 스트림. 실패는 LLMError — 호출부(service)가 폴백을 낸다.

        history 는 assistant(페르소나)/user 교대 메시지. opening 이면 history 가 비어 있고
        지시문을 user 메시지로 넣는다 (Anthropic 은 첫 메시지가 user 여야 한다)."""
        # @observe의 async-generator 래퍼는 asyncio.timeout의 task 문맥을 바꿀 수 있다.
        # 스트림 전체를 명시적 observation으로 감싸 기존 타임아웃 동작을 보존한다.
        langfuse = get_client()
        with langfuse.start_as_current_observation(
            as_type="span",
            name="practice-reply-workflow",
        ):
            with propagate_langfuse_metadata(trace_metadata):
                messages = list(history)
                if opening or not messages:
                    messages.append({"role": "user", "content": OPENING_INSTRUCTION})
                async for chunk in _stream(
                    system=system,
                    messages=messages,
                    max_tokens=300,  # 1~3문장. 넉넉히
                    timeout=None,
                    metadata=trace_metadata,
                ):
                    yield chunk
