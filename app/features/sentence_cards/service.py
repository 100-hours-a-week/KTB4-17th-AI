"""문장 판정 카드 — 카드 발급과 답변 묶음 수신.

카드는 실시간으로 발급하고, 답변은 BE 가 모아 두었다가 묶음으로 보낸다. AI 는 답변을 늦게 받으므로
"이 유저가 이미 답했는지"를 실시간으로 모른다. 그래서 next 는 응답 전 카드를 다시 돌려주지 않고
(그건 BE 가 한다) 그 유저가 아직 받지 않은 문장으로 새 카드를 만든다.

만료 판정은 도착 시각이 아니라 answered_at 기준이다. 늦게 도착해도 만료 전에 누른 답변은 유효하다.

로그 (BE 연동 확인용, `docker compose logs ai | grep "sentence card"`):
  sentence card issued / replayed / skipped(reason=daily_limit|no_sentence) / request_id conflict
  sentence card batch accepted (모두 저장) · rejected (거절 사유별 건수, WARNING) · replayed · conflict (WARNING)
  sentence verdicts changed (판정이 바뀐 문장 수) · sentence training trigger (학습 후보, 알림은 notify.py)

묶음을 받으면 새 답이 들어온 문장만 다수결로 다시 판정하고 (규칙: verdict.py), 직전 학습 후보 이후
AGREE · 확인된 DISAGREE 가 충분히 늘었으면 학습 후보(sentence_training_triggers)를 만든다. 야간 배치는 없다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.persona.schemas import SCORED, TEXTUAL

from .models import Sentence, SentenceAnswerBatch, SentenceCard, SentenceTrainingTrigger
from .repository import SentenceCardRepository
from .schemas import (
    CardStatus,
    ChoiceId,
    QuestionType,
    RejectedAnswer,
    RejectReason,
    SentenceCardAnswerBatchRequest,
    SentenceCardAnswerBatchResponse,
    SentenceCardAnswerItem,
    SentenceCardChoice,
    SentenceCardNextRequest,
    SentenceCardResponse,
)
from .verdict import MIN_VOTERS, judge, trigger_due, unreliable_users

DAILY_LIMIT = 3  # 유저당 하루 발급 상한 (초안)
CARD_TTL = timedelta(hours=24)
CHECK_RATIO = 0.1  # 검증 문항을 고를 확률
DAY_TZ = ZoneInfo("Asia/Seoul")  # "하루"의 기준

DIRECTION_QUESTION = "이런 말을 하는 사람은 어느 쪽일까요?"
AXIS_CHOICES = [
    (ChoiceId.DIRECT, "직접 말하고 있어요"),
    (ChoiceId.INFERRED, "짐작할 수 있어요"),
    (ChoiceId.NONE, "관련 없어요"),
]
UNSURE = (ChoiceId.UNSURE, "판단하기 어려워요")

logger = logging.getLogger(__name__)


class RequestIdConflict(Exception):
    """같은 request_id 로 다른 유저의 카드를 요청했다."""


class BatchConflict(Exception):
    """같은 batch_id 에 다른 내용이 왔다."""


def _aware(dt: datetime) -> datetime:
    # SQLite 는 타임존을 버리고 돌려준다. 저장은 항상 UTC 라서 UTC 로 붙인다
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def build_question(sentence: Sentence, rng: random.Random) -> tuple[str, list[SentenceCardChoice]]:
    """질문 문구와 선택지. 방향 질문은 high · low 순서를 섞고, 축 질문은 정도 순서라 섞지 않는다. 판단 어려움은 항상 끝."""
    if sentence.question_type == QuestionType.DIRECTION:
        dim = SCORED.get(sentence.dimension)
        if dim is None:
            raise ValueError(f"방향 질문은 점수 차원만 된다: {sentence.dimension}")
        pair = [(ChoiceId.HIGH, dim.high), (ChoiceId.LOW, dim.low)]
        rng.shuffle(pair)
        return DIRECTION_QUESTION, [SentenceCardChoice(choice_id=c, label=lbl) for c, lbl in [*pair, UNSURE]]
    if sentence.question_type == QuestionType.AXIS:
        label = SCORED[sentence.dimension].label if sentence.dimension in SCORED else TEXTUAL.get(sentence.dimension)
        if label is None:
            raise ValueError(f"등록되지 않은 차원: {sentence.dimension}")
        choices = [*AXIS_CHOICES, UNSURE]
        return f"이 말은 '{label}'에 대한 이야기인가요?", [
            SentenceCardChoice(choice_id=c, label=lbl) for c, lbl in choices
        ]
    raise ValueError(f"알 수 없는 질문 종류: {sentence.question_type}")


class SentenceCardService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        now: Callable[[], datetime] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.db = db
        self.repo = SentenceCardRepository(db)
        self._now = now or (lambda: datetime.now(UTC))
        self._rng = rng or random.Random()

    # ── 카드 발급 ────────────────────────────────────────
    async def next_card(self, req: SentenceCardNextRequest) -> SentenceCardResponse | None:
        request_id = str(req.request_id)
        replay = await self._replay_card(request_id, req.user_id)
        if replay is not None:
            return replay

        now = self._now()
        day_start = now.astimezone(DAY_TZ).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        if await self.repo.count_cards_since(req.user_id, day_start) >= DAILY_LIMIT:
            logger.info("sentence card skipped: user=%s reason=daily_limit", req.user_id)
            return None

        # 같은 유저의 요청 두 개가 동시에 같은 문장을 고르면 (sentence_id, user_id) 유니크에 걸린다. 다시 고른다
        for _ in range(3):
            sentence = await self._pick(req.user_id)
            if sentence is None:
                logger.info("sentence card skipped: user=%s reason=no_sentence", req.user_id)
                return None
            question, choices = build_question(sentence, self._rng)
            card = SentenceCard(
                sentence_id=sentence.id,
                user_id=req.user_id,
                shown_request_id=request_id,
                question=question,
                choices=[c.model_dump(mode="json") for c in choices],
                status=CardStatus.SHOWN.value,
                shown_at=now,
                expires_at=now + CARD_TTL,
            )
            try:
                async with self.db.begin_nested():
                    await self.repo.add_card(card)
            except IntegrityError:
                replay = await self._replay_card(request_id, req.user_id)  # 같은 request_id 가 동시에 왔다
                if replay is not None:
                    return replay
                continue
            logger.info(
                "sentence card issued: card=%s user=%s sentence=%s type=%s check=%s",
                card.id,
                req.user_id,
                sentence.id,
                sentence.question_type,
                sentence.is_check,
            )
            return self._to_response(card, sentence)
        logger.warning("sentence card skipped: user=%s reason=pick_conflict", req.user_id)
        return None

    async def _replay_card(self, request_id: str, user_id: str) -> SentenceCardResponse | None:
        card = await self.repo.get_card_by_request_id(request_id)
        if card is None:
            return None
        if card.user_id != user_id:
            logger.warning(
                "sentence card request_id conflict: request_id=%s card_user=%s request_user=%s",
                request_id,
                card.user_id,
                user_id,
            )
            raise RequestIdConflict
        logger.info("sentence card replayed: card=%s user=%s", card.id, user_id)
        sentence = await self.repo.get_sentence(card.sentence_id)
        return self._to_response(card, sentence)

    async def _pick(self, user_id: str) -> Sentence | None:
        prefer_check = self._rng.random() < CHECK_RATIO
        for is_check in (prefer_check, not prefer_check):
            candidates = await self.repo.least_shown_unseen(user_id, is_check=is_check)
            if candidates:
                return self._rng.choice(candidates)
        return None

    @staticmethod
    def _to_response(card: SentenceCard, sentence: Sentence) -> SentenceCardResponse:
        return SentenceCardResponse(
            card_id=card.id,
            sentence=sentence.sentence,
            question=card.question,
            choices=[SentenceCardChoice.model_validate(c) for c in card.choices],
            expires_at=_aware(card.expires_at),
        )

    # ── 답변 묶음 ────────────────────────────────────────
    async def submit_batch(self, req: SentenceCardAnswerBatchRequest) -> SentenceCardAnswerBatchResponse:
        batch_id = str(req.batch_id)
        payload = json.dumps(
            [a.model_dump(mode="json") for a in req.answers], sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()

        existing = await self.repo.get_batch(batch_id)
        if existing is not None:
            return self._replay_batch(existing, digest)
        batch = SentenceAnswerBatch(batch_id=batch_id, payload_hash=digest, received_at=self._now())
        try:
            async with self.db.begin_nested():
                await self.repo.add_batch(batch)
        except IntegrityError:  # 같은 batch_id 가 동시에 왔다. 먼저 끝난 쪽 응답을 돌려준다
            existing = await self.repo.get_batch(batch_id)
            if existing is None:
                raise
            return self._replay_batch(existing, digest)

        cards = await self.repo.lock_cards({a.card_id for a in req.answers})
        seen: set[str] = set()
        rejected: list[RejectedAnswer] = []
        for answer in req.answers:
            reason = self._apply(answer, cards.get(answer.card_id), seen, batch_id)
            if reason is not None:
                rejected.append(RejectedAnswer(card_id=answer.card_id, reason=reason))

        # 이번 묶음으로 새로 답이 들어온 문장만 다시 판정한다 (같은 값 재전송은 batch_id 가 바뀌지 않는다)
        touched = {card.sentence_id for card in cards.values() if card.batch_id == batch_id}
        if touched:
            await self._judge(touched)
            await self.check_trigger(f"batch:{batch_id}")

        response = SentenceCardAnswerBatchResponse(
            batch_id=req.batch_id,
            received=len(req.answers),
            accepted=len(req.answers) - len(rejected),
            rejected=rejected,
        )
        batch.response = response.model_dump(mode="json")
        await self.db.flush()
        if rejected:
            reasons = dict(Counter(r.reason.value for r in rejected))
            logger.warning(
                "sentence card batch rejected: batch=%s received=%d accepted=%d rejected=%s",
                batch_id,
                response.received,
                response.accepted,
                reasons,
            )
        else:
            logger.info(
                "sentence card batch accepted: batch=%s received=%d accepted=%d",
                batch_id,
                response.received,
                response.accepted,
            )
        return response

    # ── 다수결 판정 · 학습 트리거 ─────────────────────────
    async def _judge(self, sentence_ids: set[str]) -> None:
        sentences = await self.repo.sentences_for_update(sentence_ids)
        votes = await self.repo.answered_votes(sentence_ids)
        excluded = unreliable_users(await self.repo.check_results({user_id for _, user_id, _ in votes}))
        by_sentence: dict[str, list[str]] = defaultdict(list)
        for sentence_id, user_id, choice in votes:
            if user_id not in excluded:
                by_sentence[sentence_id].append(choice)

        changed: Counter[str] = Counter()
        for sentence in sentences:
            expected = sentence.check_answer if sentence.is_check else sentence.expected_champion
            result = judge(by_sentence[sentence.id], expected)
            if (result.verdict.value, result.consensus) != (sentence.verdict, sentence.consensus):
                sentence.reviewed_by = None  # 사람 확인은 그 판정에 대한 것이다. 판정이 바뀌면 다시 확인한다
                if result.verdict.value != sentence.verdict:
                    changed[result.verdict.value] += 1
            sentence.verdict = result.verdict.value
            sentence.consensus = result.consensus
            sentence.agreement = result.agreement
            sentence.matches_teacher = (
                result.consensus == sentence.expected_teacher
                if sentence.expected_teacher and result.consensus and result.voters >= MIN_VOTERS
                else None
            )
        if changed:
            logger.info(
                "sentence verdicts changed: %s sentences=%d excluded_users=%d",
                dict(changed),
                len(sentences),
                len(excluded),
            )

    async def check_trigger(self, source: str) -> SentenceTrainingTrigger | None:
        """직전 학습 후보 이후 AGREE · 확인된 DISAGREE 가 충분히 늘었으면 새 후보를 만든다. 묶음 수신 · 검수 반영 때 부른다."""
        current = await self.repo.trainable_counts()
        last = await self.repo.last_trigger()
        if not trigger_due(current, (last.agree, last.disagree_reviewed) if last else (0, 0)):
            return None
        trigger = SentenceTrainingTrigger(
            seq=last.seq + 1 if last else 1,
            source=source,
            agree=current[0],
            disagree_reviewed=current[1],
            created_at=self._now(),
        )
        try:
            async with self.db.begin_nested():
                await self.repo.add_trigger(trigger)
        except IntegrityError:  # 동시에 들어온 다른 묶음이 같은 번호로 먼저 만들었다
            logger.info("sentence training trigger already created: seq=%d", trigger.seq)
            return None
        logger.info(
            "sentence training trigger: seq=%d source=%s agree=%d disagree_reviewed=%d",
            trigger.seq,
            source,
            trigger.agree,
            trigger.disagree_reviewed,
        )
        return trigger

    @staticmethod
    def _replay_batch(batch: SentenceAnswerBatch, digest: str) -> SentenceCardAnswerBatchResponse:
        if batch.payload_hash != digest or batch.response is None:
            logger.warning("sentence card batch conflict: batch=%s (same batch_id, different payload)", batch.batch_id)
            raise BatchConflict
        logger.info("sentence card batch replayed: batch=%s", batch.batch_id)
        return SentenceCardAnswerBatchResponse.model_validate(batch.response)

    @staticmethod
    def _apply(
        answer: SentenceCardAnswerItem, card: SentenceCard | None, seen: set[str], batch_id: str
    ) -> RejectReason | None:
        """답변 하나를 확인하고 저장한다. 저장하지 않으면 이유를 돌려준다. 확인 순서는 API 문서 표와 같다."""
        if answer.card_id in seen:
            return RejectReason.DUPLICATE_IN_BATCH
        seen.add(answer.card_id)
        if card is None:
            return RejectReason.CARD_NOT_FOUND
        if card.user_id != answer.user_id:
            return RejectReason.NOT_CARD_OWNER
        if answer.choice_id.value not in {c["choice_id"] for c in card.choices}:
            return RejectReason.CHOICE_NOT_IN_CARD
        if answer.answered_at > _aware(card.expires_at):
            return RejectReason.ANSWERED_AFTER_EXPIRY
        if card.choice is not None:  # 다른 묶음으로 이미 왔다. 같은 값이면 재전송이라 받은 것으로 센다
            return None if card.choice == answer.choice_id.value else RejectReason.ALREADY_ANSWERED
        card.choice = answer.choice_id.value
        card.answered_at = answer.answered_at
        card.status = CardStatus.ANSWERED.value
        card.batch_id = batch_id
        return None
