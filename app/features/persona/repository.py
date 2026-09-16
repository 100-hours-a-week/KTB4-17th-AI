"""DB 접근. service.py는 SQLAlchemy를 직접 만지지 않는다."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import ConversationTurn, OnboardingSession, PersonaRecord


class PersonaRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── 세션 ──────────────────────────────────────────────

    async def create_session(self, nickname: str, total_turns: int, user_id: str | None = None) -> OnboardingSession:
        session = OnboardingSession(
            nickname=nickname,
            total_turns=total_turns,
            user_id=user_id,
            used_topic_ids=[],
            coverage={"primary": {}, "secondary": {}},
            # 비워서라도 넣어야 한다 — 생성 직후 service._history() 가 turns 를 읽는데,
            # 초기화 안 된 컬렉션은 lazy load 를 타고 async 세션에서 MissingGreenlet 이 난다
            turns=[],
        )
        self.db.add(session)
        await self.db.flush()
        return session

    async def get_session(self, session_id: str) -> OnboardingSession | None:
        stmt = (
            select(OnboardingSession)
            .where(OnboardingSession.id == session_id)
            .options(selectinload(OnboardingSession.turns))
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    # ── 턴 ────────────────────────────────────────────────

    async def add_question(
        self,
        session: OnboardingSession,
        topic_id: str,
        question: str,
        source: str,
    ) -> ConversationTurn:
        turn = ConversationTurn(
            session_id=session.id,
            turn_index=session.turn_index,
            topic_id=topic_id,
            question=question,
            question_source=source,
        )
        self.db.add(turn)

        session.pending_topic_id = topic_id
        # JSON 컬럼은 리스트를 새로 할당해야 변경이 감지된다
        session.used_topic_ids = [*session.used_topic_ids, topic_id]

        await self.db.flush()
        return turn

    async def record_answer(
        self,
        session: OnboardingSession,
        answer: str,
        tags: dict | None,
        coverage: dict,
    ) -> None:
        stmt = select(ConversationTurn).where(
            ConversationTurn.session_id == session.id,
            ConversationTurn.turn_index == session.turn_index,
        )
        turn = (await self.db.execute(stmt)).scalar_one()
        turn.answer = answer
        turn.tags = tags

        session.turn_index += 1
        session.pending_topic_id = None
        session.coverage = coverage  # 통째로 재할당

        await self.db.flush()

    # ── 페르소나 ──────────────────────────────────────────

    async def save_persona(
        self,
        session: OnboardingSession,
        scores: dict,
        texts: dict,
        confidence: dict,
    ) -> PersonaRecord:
        record = PersonaRecord(
            session_id=session.id,
            user_id=session.user_id,
            scores=scores,
            texts=texts,
            confidence=confidence,
        )
        self.db.add(record)
        session.status = "completed"
        await self.db.flush()
        return record
