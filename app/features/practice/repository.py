"""DB 접근. service.py 는 SQLAlchemy 를 직접 만지지 않는다."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import PracticeMessage, PracticeSession


class PracticeRepository:
    # 이 요청의 DB 세션을 보관
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # 연습대화 세션 row 를 만들어 flush(id 확정)까지 하고 돌려준다
    async def create_session(
        self,
        *,
        partner_persona_id: str,
        partner_user_id: str | None,
        partner_nickname: str,
        my_persona_id: str | None,
        user_id: str | None,
        my_nickname: str,
    ) -> PracticeSession:
        session = PracticeSession(
            partner_persona_id=partner_persona_id,
            partner_user_id=partner_user_id,
            partner_nickname=partner_nickname,
            my_persona_id=my_persona_id,
            user_id=user_id,
            my_nickname=my_nickname,
            messages=[],  # persona.repository 와 같은 이유 — 초기화 안 된 컬렉션은 async 에서 lazy load 를 탄다
        )
        self.db.add(session)
        await self.db.flush()
        return session

    # id 로 세션을 조회하되 메시지 목록까지 한 번에 eager load 한다
    async def get_session(self, session_id: str) -> PracticeSession | None:
        stmt = (
            select(PracticeSession)
            .where(PracticeSession.id == session_id)
            .options(selectinload(PracticeSession.messages))
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    # 메시지 한 건을 세션에 추가하고 message_count 를 1 올린다 (index 는 이 카운트를 그대로 씀)
    async def add_message(
        self, session: PracticeSession, role: str, content: str, source: str | None = None
    ) -> PracticeMessage:
        msg = PracticeMessage(
            session_id=session.id,
            index=session.message_count,
            role=role,
            content=content,
            source=source,
        )
        session.messages.append(msg)
        session.message_count += 1
        await self.db.flush()
        return msg

    # 세션 상태를 ended 로 바꾼다
    async def end_session(self, session: PracticeSession) -> None:
        session.status = "ended"
        await self.db.flush()
