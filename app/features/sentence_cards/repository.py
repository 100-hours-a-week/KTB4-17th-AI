"""DB 접근. service.py 는 SQLAlchemy 를 직접 만지지 않는다."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Sentence, SentenceAnswerBatch, SentenceCard, SentenceTrainingTrigger
from .schemas import CardStatus, Verdict


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

    # ── 다수결 판정 ─────────────────────────────────────────
    async def sentences_for_update(self, sentence_ids: set[str]) -> list[Sentence]:
        """판정을 고칠 문장을 잠근다. 같은 문장에 답한 묶음 두 개가 동시에 와도 판정을 한쪽씩 쓴다 (PostgreSQL)."""
        if not sentence_ids:
            return []
        stmt = select(Sentence).where(Sentence.id.in_(sentence_ids)).order_by(Sentence.id).with_for_update()
        return list((await self.db.execute(stmt)).scalars())

    async def answered_votes(self, sentence_ids: set[str]) -> list[tuple[str, str, str]]:
        """(sentence_id, user_id, choice). 같은 문장은 유저당 카드 1장이라 한 사람 한 표다."""
        stmt = select(SentenceCard.sentence_id, SentenceCard.user_id, SentenceCard.choice).where(
            SentenceCard.sentence_id.in_(sentence_ids), SentenceCard.status == CardStatus.ANSWERED.value
        )
        return [tuple(row) for row in (await self.db.execute(stmt)).all()]

    async def check_results(self, user_ids: set[str]) -> list[tuple[str, bool]]:
        """(user_id, 검증 문항을 맞혔는지). '판단 어려움'도 틀린 것으로 센다 — 검증 문항은 답이 뻔한 문장이다."""
        if not user_ids:
            return []
        stmt = (
            select(SentenceCard.user_id, SentenceCard.choice, Sentence.check_answer)
            .join(Sentence, Sentence.id == SentenceCard.sentence_id)
            .where(
                SentenceCard.user_id.in_(user_ids),
                SentenceCard.status == CardStatus.ANSWERED.value,
                Sentence.is_check.is_(True),
            )
        )
        return [(user_id, choice == answer) for user_id, choice, answer in (await self.db.execute(stmt)).all()]

    # ── DISAGREE 검수 ───────────────────────────────────────
    async def pending_disagreements(self) -> list[Sentence]:
        """사람 확인을 기다리는 DISAGREE (학습에 쓸 수 있는 문장만)."""
        stmt = (
            select(Sentence)
            .where(
                Sentence.verdict == Verdict.DISAGREE.value,
                Sentence.reviewed_by.is_(None),
                Sentence.active.is_(True),
                Sentence.is_check.is_(False),
            )
            .order_by(Sentence.agreement.desc(), Sentence.id)
        )
        return list((await self.db.execute(stmt)).scalars())

    # ── 학습 트리거 ─────────────────────────────────────────
    async def trainable_counts(self) -> tuple[int, int]:
        """(AGREE 수, 사람이 확인한 DISAGREE 수). 검증 문항 · 비활성 문장은 학습에 쓰지 않으므로 뺀다."""
        trainable = (Sentence.active.is_(True), Sentence.is_check.is_(False))
        agree = select(func.count()).select_from(Sentence).where(*trainable, Sentence.verdict == Verdict.AGREE.value)
        reviewed = (
            select(func.count())
            .select_from(Sentence)
            .where(*trainable, Sentence.verdict == Verdict.DISAGREE.value, Sentence.reviewed_by.is_not(None))
        )
        return int((await self.db.execute(agree)).scalar_one()), int((await self.db.execute(reviewed)).scalar_one())

    async def last_trigger(self) -> SentenceTrainingTrigger | None:
        stmt = select(SentenceTrainingTrigger).order_by(SentenceTrainingTrigger.seq.desc()).limit(1)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def add_trigger(self, trigger: SentenceTrainingTrigger) -> SentenceTrainingTrigger:
        self.db.add(trigger)
        await self.db.flush()
        return trigger

    async def unnotified_triggers(self) -> list[SentenceTrainingTrigger]:
        stmt = (
            select(SentenceTrainingTrigger)
            .where(SentenceTrainingTrigger.notified_at.is_(None))
            .order_by(SentenceTrainingTrigger.seq)
            .with_for_update(skip_locked=True)  # 알림 두 개가 동시에 돌아도 같은 트리거를 두 번 보내지 않게
        )
        return list((await self.db.execute(stmt)).scalars())
