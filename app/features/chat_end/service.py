"""채팅 종료 — 결정하는 곳.

  create_drafts(req)       1·2·3번째 답변 스크립트 생성 → 문장마다 가드레일 → PENDING 으로 저장.
                           LLM 이 실패해도 폴백 스크립트로 응답한다
  confirm_draft(draft_id)  PENDING → CONFIRMED. 멱등.
                           같은 위임에서 messages 가 이미 시작됐으면 다른 초안은 확정할 수 없다
  create_message(req)      요청 end_type 이 확정 스크립트의 end_type 과 다르면 거절한다.
                           확정 스크립트의 N번째 문장(N = 성공한 답장 수 + 1, 최대 3)을 방향으로 종료 메시지 생성 →
                           가드레일(재생성 1회) → 저장. 실패하면 FAILED 행을 남기고 MessageGenerationFailed 를 던진다
                           (폴백 문장을 응답으로 내리지 않는다. 라우트가 503 으로 바꾼다)

커밋은 라우트(api.py)가 한다.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.guardrail import AppliedText, GuardrailContext, apply_text, effective_mode
from app.core.guardrail_trace import record_guardrail
from app.core.observability import build_langfuse_metadata

from .agents import FALLBACK_SCRIPTS, DraftAgent, EndMessage, EndMessageAgent, LLMError
from .models import ChatEndDraft
from .repository import ChatEndRepository
from .schemas import (
    MAX_AI_TURNS,
    MAX_REPLY_CHARS,
    ChatEndConfirmResponse,
    ChatEndDraftsRequest,
    ChatEndDraftsResponse,
    ChatEndMessageRequest,
    ChatEndMessageResponse,
    ChatEndStatus,
    DraftStatus,
    RecentMessage,
    ScriptTurn,
)

logger = logging.getLogger(__name__)

FEATURE = "chat_end"


class DraftNotFound(Exception):
    """없는 draft_id."""


class DraftLocked(Exception):
    """messages 가 이미 시작돼 확정 스크립트를 바꿀 수 없다."""


class NoConfirmedDraft(Exception):
    """이 위임에 확정된 스크립트가 없다. 먼저 drafts → confirm 을 거쳐야 한다."""


class MessageGenerationFailed(Exception):
    """LLM 장애·가드레일 차단으로 종료 메시지를 만들지 못했다. FAILED 행은 이미 저장됐고 턴은 쓰지 않는다."""

    def __init__(self, ai_turn: int) -> None:
        self.ai_turn = ai_turn
        super().__init__(f"ai_turn={ai_turn}")


class EndTypeMismatch(Exception):
    """messages 요청의 end_type 이 확정 스크립트의 end_type 과 다르다."""

    def __init__(self, confirmed: str, requested: str) -> None:
        self.confirmed = confirmed
        self.requested = requested
        super().__init__(f"confirmed={confirmed} requested={requested}")


# 가드레일 문맥. 대화 양쪽이 실제로 한 말은 언급해도 되므로 user_texts 에 전부 넣는다
def _guardrail_ctx(recent: list[RecentMessage]) -> GuardrailContext:
    return GuardrailContext(
        surface="chat_end_message",
        speaker_name="요청자",
        user_texts=[m.content for m in recent],
        max_chars=MAX_REPLY_CHARS,
        task="single_turn",
    )


def _is_fallback(checked: AppliedText) -> bool:
    return bool(checked.result and checked.result.status == "FALLBACK")


def _drafts_response(row: ChatEndDraft) -> ChatEndDraftsResponse:
    return ChatEndDraftsResponse(
        room_id=row.room_id,
        draft_id=row.id,
        status=DraftStatus(row.status),
        turns=[ScriptTurn(turn=i, message=m) for i, m in enumerate(row.turns, start=1)],
    )


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
        turns, source = await self.draft_agent.generate(
            end_type=req.end_type, recent=req.recent_messages, metadata=metadata
        )

        ctx = _guardrail_ctx(req.recent_messages)
        checked_turns: list[str] = []
        for i, text in enumerate(turns):
            fallback = FALLBACK_SCRIPTS[req.end_type][i]  # 가드레일에 걸리면 그 턴의 폴백 문장으로 바꾼다
            checked = await apply_text(text, ctx, user_key=req.requester_user_id, fallback=fallback)
            await self._record("drafts", req.delegation_id, req.requester_user_id, checked, text)
            if _is_fallback(checked):
                source = "fallback"
            checked_turns.append(checked.text)

        row = await self.repo.save_draft(
            room_id=req.room_id,
            delegation_id=req.delegation_id,
            requester_user_id=req.requester_user_id,
            end_type=req.end_type.value,
            turns=checked_turns,
            source=source,
        )
        return _drafts_response(row)

    async def confirm_draft(self, draft_id: str) -> ChatEndConfirmResponse:
        draft = await self.repo.get_draft(draft_id)
        if draft is None:
            raise DraftNotFound(draft_id)
        # 같은 위임의 답장 생성·다른 확정과 직렬화한 뒤 잠금 시점의 최신 상태로 판단한다
        await self.repo.lock_delegation(draft.room_id, draft.delegation_id)
        await self.db.refresh(draft)
        if draft.status != DraftStatus.CONFIRMED.value:  # 이미 확정이면 아무것도 하지 않는다(멱등)
            started = await self.repo.count_successful_messages(
                room_id=draft.room_id, delegation_id=draft.delegation_id
            )
            if started:
                raise DraftLocked(draft_id)
            await self.repo.confirm_draft(draft)
        return ChatEndConfirmResponse(
            draft_id=draft.id, delegation_id=draft.delegation_id, status=DraftStatus.CONFIRMED
        )

    async def create_message(self, req: ChatEndMessageRequest) -> ChatEndMessageResponse:
        # 같은 위임의 동시 답장·확정을 직렬화한다. 라우트가 commit 할 때까지 잠금을 유지해 턴 번호 중복과 스크립트 중도 교체를 막는다
        await self.repo.lock_delegation(req.room_id, req.delegation_id)
        draft = await self.repo.get_confirmed_draft(req.room_id, req.delegation_id)
        if draft is None:
            raise NoConfirmedDraft(req.delegation_id)
        if req.end_type.value != draft.end_type:
            logger.warning(
                "chat_end end_type mismatch room=%s delegation=%s confirmed=%s requested=%s",
                req.room_id,
                req.delegation_id,
                draft.end_type,
                req.end_type.value,
            )
            raise EndTypeMismatch(draft.end_type, req.end_type.value)
        done = await self.repo.count_successful_messages(room_id=req.room_id, delegation_id=req.delegation_id)
        ai_turn = min(done + 1, MAX_AI_TURNS)  # 이미 3번 끝났어도 더 늘리지 않고 종료 답장으로 응답
        direction = draft.turns[ai_turn - 1]  # 이번 턴에 프롬프트로 보내는 문장은 이것 하나뿐이다
        fallback = direction

        metadata = build_langfuse_metadata(
            feature=FEATURE,
            operation="message",
            user_id=req.user_id,
            session_id=str(req.delegation_id),
            end_type=req.end_type.value,
        )

        async def generate(notice: str = "") -> EndMessage:
            return await self.message_agent.generate(
                end_type=req.end_type,
                recent=req.recent_messages,
                endings=[direction],
                ai_turn=ai_turn,
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
            text, end_reason, status, source = "", None, ChatEndStatus.FAILED, "failed"  # 나가는 문장이 없다
        end_turns = ai_turn if status is ChatEndStatus.SUCCESS else 0

        row = await self.repo.save_message(
            room_id=req.room_id,
            delegation_id=req.delegation_id,
            user_id=req.user_id,
            target_user_id=req.target_user_id,
            end_type=req.end_type.value,
            draft_id=draft.id,
            direction=direction,
            ai_response=text,
            status=status.value,
            end_turns=end_turns,
            end_reason=end_reason,
            source=source,
        )
        if status is ChatEndStatus.FAILED:  # 폴백 문장을 응답으로 내리지 않는다. 행은 남겨 실패율을 셀 수 있게 한다
            raise MessageGenerationFailed(ai_turn)
        return ChatEndMessageResponse(
            room_id=req.room_id,
            message_id=row.id,
            ai_response=text,
            status=status,
            ai_turn_count=ai_turn,
            is_final=status is ChatEndStatus.SUCCESS and ai_turn == MAX_AI_TURNS,
            end_turns=end_turns,
            end_reason=end_reason,
        )
