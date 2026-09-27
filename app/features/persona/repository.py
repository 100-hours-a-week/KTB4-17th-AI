"""DB 접근. service.py는 SQLAlchemy를 직접 만지지 않는다."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import OnboardingSession, OnboardingTurn, PersonaRecord


class PersonaRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── 세션 ──────────────────────────────────────────────

    async def create_session(self, nickname: str, total_turns: int, user_id: str) -> OnboardingSession:
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

    async def get_session_for_update(self, session_id: str) -> OnboardingSession | None:
        """동일 세션의 동시 build가 같은 version을 만들지 못하도록 세션 행을 잠근다."""
        stmt = (
            select(OnboardingSession)
            .where(OnboardingSession.id == session_id)
            .options(selectinload(OnboardingSession.turns))
            .with_for_update()
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    # ── 턴 ────────────────────────────────────────────────

    async def add_question(
        self,
        session: OnboardingSession,
        topic_id: str,
        question: str,
        source: str,
    ) -> OnboardingTurn:
        turn = OnboardingTurn(
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
        stmt = select(OnboardingTurn).where(
            OnboardingTurn.session_id == session.id,
            OnboardingTurn.turn_index == session.turn_index,
        )
        turn = (await self.db.execute(stmt)).scalar_one()
        turn.answer = answer
        turn.tags = tags

        session.turn_index += 1
        session.pending_topic_id = None
        session.coverage = coverage  # 통째로 재할당

        await self.db.flush()

    async def skip_question(self, session: OnboardingSession) -> None:
        """대기 중인 질문을 답 없이 넘긴다. 턴은 소비되고 커버리지는 그대로."""
        stmt = select(OnboardingTurn).where(
            OnboardingTurn.session_id == session.id,
            OnboardingTurn.turn_index == session.turn_index,
        )
        turn = (await self.db.execute(stmt)).scalar_one()
        turn.skipped = True

        session.turn_index += 1
        session.pending_topic_id = None
        await self.db.flush()

    async def finish_early(self, session: OnboardingSession) -> None:
        """남은 질문을 포기하고 대화를 닫는다. 대기 중인 질문이 있으면 건너뛴 것으로."""
        if session.pending_topic_id is not None:
            await self.skip_question(session)
        # total_turns 를 지금까지로 줄이면 is_done 판정과 진행 표시가 자연히 맞는다
        session.total_turns = session.turn_index
        await self.db.flush()

    async def add_supplement(
        self,
        session: OnboardingSession,
        dimension: str,
        question: str,
        answer: str,
        coverage: dict,
    ) -> OnboardingTurn:
        """보강 문답 한 건. 온보딩 턴 뒤에 이어 붙고, 진행 카운터는 건드리지 않는다."""
        turn = OnboardingTurn(
            session_id=session.id,
            turn_index=len(session.turns),
            topic_id=f"supplement:{dimension}",
            question=question,
            answer=answer,
            question_source="bank",
        )
        session.turns.append(turn)
        session.coverage = coverage
        await self.db.flush()
        return turn

    # ── 페르소나 ──────────────────────────────────────────

    async def latest_persona(self, session_id: str) -> PersonaRecord | None:
        stmt = (
            select(PersonaRecord)
            .where(PersonaRecord.session_id == session_id)
            .order_by(PersonaRecord.version.desc())
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_persona(self, persona_id: str) -> PersonaRecord | None:
        return await self.db.get(PersonaRecord, persona_id)

    async def get_persona_for_update(self, persona_id: str) -> PersonaRecord | None:
        """확정 요청끼리 같은 초안을 동시에 갱신하지 못하도록 행 잠금으로 조회한다."""
        stmt = select(PersonaRecord).where(PersonaRecord.id == persona_id).with_for_update()
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def latest_confirmed_persona(self, session_id: str) -> PersonaRecord | None:
        stmt = (
            select(PersonaRecord)
            .where(PersonaRecord.session_id == session_id, PersonaRecord.is_confirmed.is_(True))
            .order_by(PersonaRecord.confirmed_at.desc(), PersonaRecord.version.desc())
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def latest_persona_for_user(self, user_id: str) -> PersonaRecord | None:
        """그 사용자가 마지막으로 확정한 페르소나. 미확정 초안은 노출하지 않는다."""
        stmt = (
            select(PersonaRecord)
            .where(PersonaRecord.user_id == user_id, PersonaRecord.is_confirmed.is_(True))
            .order_by(PersonaRecord.confirmed_at.desc(), PersonaRecord.created_at.desc())
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def save_confirmation(
        self,
        record: PersonaRecord,
        mbti: str,
        confirmed_at: datetime,
    ) -> PersonaRecord:
        """확정 상태와 MBTI를 같은 페르소나 행에 저장한다."""
        record.is_confirmed = True
        record.confirmed_at = confirmed_at
        record.mbti = mbti

        await self.db.flush()
        return record

    async def get_session_brief(self, session_id: str) -> OnboardingSession | None:
        """turns 를 안 싣는 가벼운 조회. 닉네임만 필요할 때 (simulation·practice)."""
        return await self.db.get(OnboardingSession, session_id)

    async def latest_before(self, record: PersonaRecord) -> PersonaRecord | None:
        if record.previous_id is None:
            return None
        return await self.db.get(PersonaRecord, record.previous_id)

    async def save_persona(
        self,
        session: OnboardingSession,
        scores: dict,
        texts: dict,
        confidence: dict,
        narrative: dict | None = None,
    ) -> tuple[PersonaRecord, PersonaRecord | None]:
        """새 버전을 추가한다. (새 행, 직전 행) — 직전 행은 변화 계산용."""
        previous = await self.latest_persona(session.id)
        record = PersonaRecord(
            session_id=session.id,
            user_id=session.user_id,
            scores=scores,
            texts=texts,
            confidence=confidence,
            narrative=narrative,
            version=(previous.version + 1) if previous else 1,
            previous_id=previous.id if previous else None,
            is_confirmed=False,
            confirmed_at=None,
            mbti=None,
        )
        self.db.add(record)
        session.status = "completed"
        await self.db.flush()
        return record, previous
