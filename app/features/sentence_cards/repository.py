"""DB 접근. service.py 는 SQLAlchemy 를 직접 만지지 않는다."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Sentence, SentenceAnswerBatch, SentenceCard


class SentenceCardRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_card_by_request_id(self, request_id: str) -> SentenceCard | None:
        stmt = select(SentenceCard).where(SentenceCard.shown_request_id == request_id)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_sentence(self, sentence_id: str) -> Sentence | None:
        return await self.db.get(Sentence, sentence_id)

    # 이 유저에게 since 이후 발급한 카드 수 (하루 노출 상한)
    async def count_cards_since(self, user_id: str, since: datetime) -> int:
        stmt = (
            select(func.count())
            .select_from(SentenceCard)
            .where(SentenceCard.user_id == user_id, SentenceCard.shown_at >= since)
        )
        return int((await self.db.execute(stmt)).scalar_one())

    async def least_shown_unseen(self, user_id: str, *, is_check: bool, limit: int = 50) -> list[Sentence]:
        """이 유저가 아직 받지 않은 활성 문장 중 노출이 가장 적은 것들 (같은 노출 수끼리). 표가 고르게 쌓이게 한다."""
        seen = select(SentenceCard.sentence_id).where(SentenceCard.user_id == user_id)
        shown = func.count(SentenceCard.id).label("shown")
        stmt = (
            select(Sentence, shown)
            .outerjoin(SentenceCard, SentenceCard.sentence_id == Sentence.id)
            .where(Sentence.active.is_(True), Sentence.is_check.is_(is_check), Sentence.id.not_in(seen))
            .group_by(Sentence.id)
            .order_by(shown, Sentence.id)
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).all()
        if not rows:
            return []
        fewest = rows[0].shown
        return [row.Sentence for row in rows if row.shown == fewest]

    async def add_card(self, card: SentenceCard) -> SentenceCard:
        self.db.add(card)
        await self.db.flush()
        return card

    async def lock_cards(self, card_ids: set[str]) -> dict[str, SentenceCard]:
        """묶음에 든 카드를 id 순으로 잠근다. 같은 카드를 담은 묶음 두 개가 동시에 와도 한쪽씩 처리된다 (PostgreSQL)."""
        if not card_ids:
            return {}
        stmt = select(SentenceCard).where(SentenceCard.id.in_(card_ids)).order_by(SentenceCard.id).with_for_update()
        return {card.id: card for card in (await self.db.execute(stmt)).scalars()}

    async def get_batch(self, batch_id: str) -> SentenceAnswerBatch | None:
        return await self.db.get(SentenceAnswerBatch, batch_id)

    async def add_batch(self, batch: SentenceAnswerBatch) -> SentenceAnswerBatch:
        self.db.add(batch)
        await self.db.flush()
        return batch
