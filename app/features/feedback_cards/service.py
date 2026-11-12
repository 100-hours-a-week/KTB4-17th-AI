from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from .models import FeedbackCard, FeedbackCardAnswer
from .repository import FeedbackCardRepository
from .schemas import FeedbackCardAnswerRequest, FeedbackCardAnswerResponse, FeedbackCardResponse

CARD_TTL = timedelta(hours=24)


def _aware(value: datetime) -> datetime:
    # SQLite는 타임존을 보존하지 않는다. 모든 저장 시각은 UTC로 정규화한다.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class CardNotFound(Exception):
    pass


class NotCardOwner(Exception):
    pass


class CardConflict(Exception):
    pass


class FeedbackCardService:
    def __init__(self, db: AsyncSession, *, now: Callable[[], datetime] | None = None) -> None:
        self.db = db
        self.repo = FeedbackCardRepository(db)
        self._now = now or (lambda: datetime.now(UTC))

    async def next_card(self, user_id: str) -> FeedbackCardResponse | None:
        await self.repo.lock_user(user_id)
        now = self._now()
        await self.repo.expire(user_id, now)
        card = await self.repo.next_with_status(user_id, "SHOWN")
        if card is None:
            card = await self.repo.next_with_status(user_id, "READY")
            if card is None:
                return None
            card.status = "SHOWN"
            card.shown_at = now
            card.expires_at = min(_aware(card.expires_at), now + CARD_TTL)
            await self.db.flush()
        return self._response(card)

    async def answer(self, card_id: str, req: FeedbackCardAnswerRequest) -> FeedbackCardAnswerResponse:
        await self.repo.lock_user(req.user_id)
        card = await self.repo.get_card(card_id)
        if card is None:
            raise CardNotFound
        if card.user_id != req.user_id:
            raise NotCardOwner

        # 성공한 답변은 만료 뒤에 재전송되어도 동일하게 성공한다.
        previous = await self.repo.get_answer(card_id)
        if previous is not None:
            if (
                previous.user_id != req.user_id
                or previous.answer != req.answer
                or previous.correction_text != req.correction_text
                or _aware(previous.answered_at) != req.answered_at
                or previous.consent_version != req.consent_version
            ):
                raise CardConflict("card already answered")
            return FeedbackCardAnswerResponse(card_id=card_id)

        # 클라이언트 시각으로 만료를 우회하지 못하도록 서버 시각으로 판정한다.
        if card.status != "SHOWN" or self._now() >= _aware(card.expires_at):
            raise CardConflict("card expired")
        card.status = "ANSWERED"
        await self.repo.save_answer(
            FeedbackCardAnswer(
                card_id=card_id,
                user_id=req.user_id,
                answer=req.answer.value,
                correction_text=req.correction_text,
                answered_at=req.answered_at.astimezone(UTC),
                consent_version=req.consent_version,
            )
        )
        return FeedbackCardAnswerResponse(card_id=card_id)

    @staticmethod
    def _response(card: FeedbackCard) -> FeedbackCardResponse:
        return FeedbackCardResponse(
            card_id=card.id,
            context_question=card.context_question,
            utterance=card.utterance,
            question=card.question,
            expires_at=_aware(card.expires_at),
        )
