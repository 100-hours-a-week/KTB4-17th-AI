"""DB 접근. service.py 는 SQLAlchemy 를 직접 만지지 않는다."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ChatEndDraft, ChatEndMessage


class ChatEndRepository:
    # 이 요청의 DB 세션을 보관
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # 이 위임에서 이미 성공한 AI 답장 수. 다음 턴 번호 = 이 값 + 1 (FAILED 는 턴을 쓰지 않는다)
    async def count_successful_messages(self, *, room_id: int, delegation_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(ChatEndMessage)
            .where(
                ChatEndMessage.room_id == room_id,
                ChatEndMessage.delegation_id == delegation_id,
                ChatEndMessage.status == "SUCCESS",
            )
        )
        return int((await self.db.execute(stmt)).scalar_one())

    # 스크립트 한 건을 PENDING 으로 저장하고 flush(id 확정)까지 한다
    async def save_draft(
        self,
        *,
        room_id: int,
        delegation_id: int,
        requester_user_id: str,
        end_type: str,
        turns: list[str],
        source: str,
    ) -> ChatEndDraft:
        row = ChatEndDraft(
            room_id=room_id,
            delegation_id=delegation_id,
            requester_user_id=requester_user_id,
            end_type=end_type,
            turns=turns,
            source=source,
            status="PENDING",
        )
        self.db.add(row)
        await self.db.flush()
        return row

    # draft_id 로 한 건. 없으면 None
    async def get_draft(self, draft_id: str) -> ChatEndDraft | None:
        return await self.db.get(ChatEndDraft, draft_id)

    # 이 방·위임의 확정(CONFIRMED) 스크립트. 없으면 None (부분 유니크 인덱스라 많아야 1건)
    async def get_confirmed_draft(self, room_id: int, delegation_id: int) -> ChatEndDraft | None:
        stmt = select(ChatEndDraft).where(
            ChatEndDraft.room_id == room_id,
            ChatEndDraft.delegation_id == delegation_id,
            ChatEndDraft.status == "CONFIRMED",
        )
        return (await self.db.execute(stmt)).scalars().first()

    # 스크립트를 확정한다. 같은 위임의 기존 CONFIRMED 를 먼저 PENDING 으로 내려 부분 유니크 인덱스를 지킨다
    async def confirm_draft(self, draft: ChatEndDraft) -> ChatEndDraft:
        await self.db.execute(
            update(ChatEndDraft)
            .where(
                ChatEndDraft.delegation_id == draft.delegation_id,
                ChatEndDraft.status == "CONFIRMED",
                ChatEndDraft.id != draft.id,
            )
            .values(status="PENDING", confirmed_at=None)
        )
        draft.status = "CONFIRMED"
        draft.confirmed_at = datetime.now(UTC)
        await self.db.flush()
        return draft

    # 종료 메시지 호출 한 건을 저장하고 flush 해 message_id 로 쓸 id 를 확정한다
    async def save_message(
        self,
        *,
        room_id: int,
        delegation_id: int,
        user_id: str,
        target_user_id: str,
        end_type: str,
        draft_id: str | None,
        direction: str | None,
        ai_response: str,
        status: str,
        end_turns: int,
        end_reason: str | None,
        source: str,
    ) -> ChatEndMessage:
        row = ChatEndMessage(
            room_id=room_id,
            delegation_id=delegation_id,
            user_id=user_id,
            target_user_id=target_user_id,
            end_type=end_type,
            draft_id=draft_id,
            direction=direction,
            ai_response=ai_response,
            status=status,
            end_turns=end_turns,
            end_reason=end_reason,
            source=source,
        )
        self.db.add(row)
        await self.db.flush()
        return row
