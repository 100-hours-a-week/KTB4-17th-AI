from __future__ import annotations

import hashlib
from datetime import datetime

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import FeedbackCard, FeedbackCardAnswer


class FeedbackCardRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def lock_user(self, user_id: str) -> None:
        """노출·답변을 같은 유저 단위로 직렬화한다. 락은 라우트의 커밋/롤백까지 유지된다."""
        if self.db.get_bind().dialect.name == "postgresql":
            digest = hashlib.blake2b(f"feedback-cards:{user_id}".encode(), digest_size=8).digest()
            await self.db.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": int.from_bytes(digest, signed=True)}
            )
        else:
            # SQLite의 첫 쓰기가 DB 쓰기 락을 얻는다. 후보가 없어도 SELECT보다 먼저 실행해야 한다.
            await self.db.execute(
                update(FeedbackCard).where(FeedbackCard.user_id == user_id).values(status=FeedbackCard.status)
            )

    async def expire(self, user_id: str, now: datetime) -> None:
        await self.db.execute(
            update(FeedbackCard)
            .where(
                FeedbackCard.user_id == user_id,
                FeedbackCard.status.in_(("READY", "SHOWN")),
                FeedbackCard.expires_at <= now,
            )
            .values(status="EXPIRED")
        )

    async def next_with_status(self, user_id: str, status: str) -> FeedbackCard | None:
        stmt = (
            select(FeedbackCard)
            .where(FeedbackCard.user_id == user_id, FeedbackCard.status == status)
            .order_by(FeedbackCard.created_at, FeedbackCard.id)
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_card(self, card_id: str) -> FeedbackCard | None:
        return await self.db.get(FeedbackCard, card_id, populate_existing=True)

    async def get_answer(self, card_id: str) -> FeedbackCardAnswer | None:
        return await self.db.get(FeedbackCardAnswer, card_id)

    async def save_answer(self, answer: FeedbackCardAnswer) -> None:
        self.db.add(answer)
        await self.db.flush()
