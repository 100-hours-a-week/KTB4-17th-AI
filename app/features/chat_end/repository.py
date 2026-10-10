"""DB 접근. service.py 는 SQLAlchemy 를 직접 만지지 않는다."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from .models import ChatEndDraft, ChatEndMessage


class ChatEndRepository:
    # 이 요청의 DB 세션을 보관
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # 초안 호출 한 건을 저장하고 flush(id 확정)까지 한다
    async def save_draft(
        self,
        *,
        room_id: int,
        delegation_id: int,
        requester_user_id: str,
        end_type: str,
        ending_messages: list[str],
        source: str,
    ) -> ChatEndDraft:
        row = ChatEndDraft(
            room_id=room_id,
            delegation_id=delegation_id,
            requester_user_id=requester_user_id,
            end_type=end_type,
            ending_messages=ending_messages,
            source=source,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    # 종료 메시지 호출 한 건을 저장하고 flush 해 message_id 로 쓸 id 를 확정한다
    async def save_message(
        self,
        *,
        room_id: int,
        delegation_id: int,
        user_id: str,
        target_user_id: str,
        end_type: str,
        ending_messages: list[str],
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
            ending_messages=ending_messages,
            ai_response=ai_response,
            status=status,
            end_turns=end_turns,
            end_reason=end_reason,
            source=source,
        )
        self.db.add(row)
        await self.db.flush()
        return row
