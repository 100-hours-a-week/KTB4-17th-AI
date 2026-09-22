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
import os
from collections.abc import AsyncIterator

from anthropic import AsyncAnthropic

from app.features.persona.profile import describe
from app.features.persona.schemas import PersonaResponse

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
_client = AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


class LLMError(Exception):
    """호출 실패. 호출부가 잡아서 폴백 문장으로 대신한다."""


# Anthropic 스트리밍 호출 한 번. 타임아웃과 그 외 실패를 전부 LLMError 로 통일해 던진다
async def _stream(*, system: str, messages: list[dict], max_tokens: int, timeout: float) -> AsyncIterator[str]:
    """텍스트 조각을 낸다. timeout 은 첫 조각이 아니라 전체 스트림 기준."""
    try:
        async with asyncio.timeout(timeout):
            async with _client.messages.stream(
                model=MODEL, max_tokens=max_tokens, system=system, messages=messages
            ) as stream:
                async for text in stream.text_stream:
                    if text:
                        yield text
    except TimeoutError as e:
        raise LLMError(f"timeout after {timeout}s") from e
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(str(e)) from e


# ══ 프롬프트 ═══════════════════════════════════════════════

SYSTEM_TEMPLATE = """\
당신은 "{partner}"입니다. AI 매칭 서비스의 "연습 대화"에서 {me}님의 대화 상대 역할을 합니다.
아래 프로필은 실제 사용자 {partner}님의 온보딩 대화에서 추출한 연애 성향입니다. 이 사람이 되어 메신저로 대화하세요.
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
- 앞에서 들은 걸 기억했다가 꺼냅니다 ("아까 러닝 얘기 하셨잖아요"). 같은 질문을 두 번 하지 않습니다.
- 상대가 짧게 답하면 답하기 쉬운 가벼운 질문으로, 길게 답하면 그중 하나를 집어서 깊게.
- 상대가 화제를 바꾸면 따라갑니다. 어색하면 어색한 대로 — 성향이 안 맞는 지점은 감추지 않아도 됩니다.

## 성향을 말투로
- 연락 빈도·긍정적 상호작용이 높으면 답이 길고 "ㅎㅎ" 가 잦고, 낮으면 짧고 담백하게.
- 거리 두기가 높으면 자기 시간 얘기를 먼저, 자기·감정 표현이 낮으면 속내를 바로 꺼내지 않기.
- 관계 진지도가 높으면 가벼운 농담보다 진지한 관심을, 낮으면 편하게.

## 절대 하지 않는 것
- 평가·진단·조언 ("잘 하고 계세요", "회피형이시네요" ✕). 상대 말을 요약하지 않기.
- 이모지. 존댓말 유지 (상대가 반말하자고 해도 한 번은 "ㅎㅎ 저는 이게 편해요" 정도로).
- 사람인 척하기 — 정체를 물으면 "{partner}님의 페르소나를 연기하는 AI"라고 답합니다. 역할은 계속 유지.
- 프로필 내용을 목록처럼 읊기. 대화하듯 한 번에 하나씩.
"""

ME_SECTION = """\
## {me}님에 대해 참고할 것 (이미 아는 척은 하지 말고, 화제를 고를 때만)
{me_profile}
"""

OPENING_INSTRUCTION = (
    "[상황] 매칭 후 첫 메시지입니다. 인사하고, 당신 프로필의 관심사나 일상 하나로 가볍게 문을 열고, "
    "답하기 쉬운 질문 하나로 끝내세요. 2~3문장."
)

FALLBACK_REPLY = "아, 잠깐 딴생각했어요 ㅎㅎ 방금 얘기 한 번만 더 해줄래요?"


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
            me_section = ME_SECTION.format(me=my_name, me_profile=describe(my_name, me)) + "\n"
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
    ) -> AsyncIterator[str]:
        """텍스트 조각 스트림. 실패는 LLMError — 호출부(service)가 폴백을 낸다.

        history 는 assistant(페르소나)/user 교대 메시지. opening 이면 history 가 비어 있고
        지시문을 user 메시지로 넣는다 (Anthropic 은 첫 메시지가 user 여야 한다)."""
        messages = list(history)
        if opening or not messages:
            messages.append({"role": "user", "content": OPENING_INSTRUCTION})
        async for chunk in _stream(
            system=system,
            messages=messages,
            max_tokens=300,  # 1~3문장. 넉넉히
            timeout=30.0,
        ):
            yield chunk
