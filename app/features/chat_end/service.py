"""채팅 종료 — 결정하는 곳.

  create_drafts(req)  초안 3개 생성 → 초안마다 가드레일 → 저장. LLM 이 실패해도 폴백 3개로 200
  create_message(req) 종료 메시지 생성 → 가드레일(재생성 1회) → 저장. 실패하면 status=FAILED,
                      ai_response 는 고른 초안 첫 문장 (엔진 기본 폴백 문장이 종료 메시지로 나가면 안 된다)

커밋은 라우트(api.py)가 한다.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.guardrail import AppliedText, GuardrailContext, apply_text, effective_mode
from app.core.guardrail_trace import record_guardrail
from app.core.observability import build_langfuse_metadata

from .agents import FALLBACK_DRAFTS, DraftAgent, EndMessage, EndMessageAgent, LLMError
from .repository import ChatEndRepository
from .schemas import (
    MAX_ENDING_LEN,
    ChatEndDraftsRequest,
    ChatEndDraftsResponse,
    ChatEndMessageRequest,
    ChatEndMessageResponse,
    ChatEndStatus,
    EndType,
    RecentMessage,
)

logger = logging.getLogger(__name__)

FEATURE = "chat_end"


# 가드레일 문맥. 대화 양쪽이 실제로 한 말은 언급해도 되므로 user_texts 에 전부 넣는다
def _guardrail_ctx(recent: list[RecentMessage]) -> GuardrailContext:
    return GuardrailContext(
        surface="chat_end_message",
        speaker_name="요청자",
        user_texts=[m.content for m in recent],
        max_chars=MAX_ENDING_LEN,
        task="single_turn",
    )


# 아직 쓰지 않은 폴백 초안 하나. 같은 문장이 두 번 나가지 않게 한다
def _spare_fallback(end_type: EndType, used: set[str]) -> str:
    for fb in FALLBACK_DRAFTS[end_type]:
        if fb not in used:
            return fb
    return FALLBACK_DRAFTS[end_type][0]


def _is_fallback(checked: AppliedText) -> bool:
    return bool(checked.result and checked.result.status == "FALLBACK")


class ChatEndService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = ChatEndRepository(db)
        self.draft_agent = DraftAgent()
        self.message_agent = EndMessageAgent()

    async def _record(self, operation: str, delegation_id: int, user_id: str, checked: AppliedText, text: str) -> None:
        await record_guardrail(
            self.db,
            feature=FEATURE,
            operation=operation,
            session_id=str(delegation_id),
            user_id=user_id,
            mode=effective_mode(user_id),
            result=checked.result,
            initial_text=text,
        )

    async def create_drafts(self, req: ChatEndDraftsRequest) -> ChatEndDraftsResponse:
        metadata = build_langfuse_metadata(
            feature=FEATURE,
            operation="drafts",
            user_id=req.requester_user_id,
            session_id=str(req.delegation_id),
            end_type=req.end_type.value,
        )
        drafts, source = await self.draft_agent.generate(
            end_type=req.end_type, recent=req.recent_messages, metadata=metadata
        )

        ctx = _guardrail_ctx(req.recent_messages)
        checked_drafts: list[str] = []
        for draft in drafts:
            spare = _spare_fallback(req.end_type, set(drafts) | set(checked_drafts))
            checked = await apply_text(draft, ctx, user_key=req.requester_user_id, fallback=spare)
            await self._record("drafts", req.delegation_id, req.requester_user_id, checked, draft)
            if _is_fallback(checked):
                source = "fallback"
            checked_drafts.append(checked.text)

        await self.repo.save_draft(
            room_id=req.room_id,
            delegation_id=req.delegation_id,
            requester_user_id=req.requester_user_id,
            end_type=req.end_type.value,
            ending_messages=checked_drafts,
            source=source,
        )
        return ChatEndDraftsResponse(room_id=req.room_id, ending_messages=checked_drafts)

    async def create_message(self, req: ChatEndMessageRequest) -> ChatEndMessageResponse:
        metadata = build_langfuse_metadata(
            feature=FEATURE,
            operation="message",
            user_id=req.user_id,
            session_id=str(req.delegation_id),
            end_type=req.end_type.value,
        )
        fallback = req.ending_messages[0]

        async def generate(notice: str = "") -> EndMessage:
            return await self.message_agent.generate(
                end_type=req.end_type,
                recent=req.recent_messages,
                endings=req.ending_messages,
                notice=notice,
                metadata=metadata,
            )

        result: EndMessage | None
        try:
            result = await generate()
        except LLMError as e:
            logger.warning("chat_end message LLM failed: %s", e)
            result = None

        ok = False
        if result is not None:
            # 재생성 실패(LLMError)는 엔진이 잡아 fallback 으로 바꾼다.
            # 재생성 결과로 result 를 바꿔 둬야 응답 문장과 end_reason 이 같은 생성에서 나온다
            async def regenerate(notice: str) -> str:
                nonlocal result
                result = await generate(notice)
                return result.ai_response

            initial_text = result.ai_response  # 추적에는 재생성 전 문장을 남긴다
            checked = await apply_text(
                initial_text,
                _guardrail_ctx(req.recent_messages),
                user_key=req.user_id,
                regenerate=regenerate,
                fallback=fallback,
            )
            await self._record("message", req.delegation_id, req.user_id, checked, initial_text)
            ok = not _is_fallback(checked)

        if ok and result is not None:
            text, end_reason, status, source = checked.text, result.end_reason, ChatEndStatus.SUCCESS, "llm"
        else:
            text, end_reason, status, source = fallback, None, ChatEndStatus.FAILED, "fallback"
        end_turns = 1 if status is ChatEndStatus.SUCCESS else 0

        row = await self.repo.save_message(
            room_id=req.room_id,
            delegation_id=req.delegation_id,
            user_id=req.user_id,
            target_user_id=req.target_user_id,
            end_type=req.end_type.value,
            ending_messages=list(req.ending_messages),
            ai_response=text,
            status=status.value,
            end_turns=end_turns,
            end_reason=end_reason,
            source=source,
        )
        return ChatEndMessageResponse(
            room_id=req.room_id,
            message_id=row.id,
            ai_response=text,
            status=status,
            end_turns=end_turns,
            end_reason=end_reason,
        )
