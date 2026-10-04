"""DB 접근. service.py 는 SQLAlchemy 를 직접 만지지 않는다."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import SimulationRecord


class SimulationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def save(
        self,
        *,
        persona_a_id: str,
        persona_b_id: str,
        user_id_a: str,
        user_id_b: str,
        nickname_a: str,
        nickname_b: str,
        turns: int,
        transcript: list[dict],
        report: dict,
        narrative_source: str,
    ) -> SimulationRecord:
        record = SimulationRecord(
            persona_a_id=persona_a_id,
            persona_b_id=persona_b_id,
            user_id_a=user_id_a,
            user_id_b=user_id_b,
            nickname_a=nickname_a,
            nickname_b=nickname_b,
            turns=turns,
            transcript=transcript,
            report=report,
            narrative_source=narrative_source,
        )
        self.db.add(record)
        await self.db.flush()
        return record

    async def get(self, simulation_id: str) -> SimulationRecord | None:
        return await self.db.get(SimulationRecord, simulation_id)

    async def list_for_user(self, user_id: str, limit: int = 20) -> list[SimulationRecord]:
        """내가 a 든 b 든 — 상대가 돌린 시뮬레이션도 내 목록에 보인다."""
        stmt = (
            select(SimulationRecord)
            .where(or_(SimulationRecord.user_id_a == user_id, SimulationRecord.user_id_b == user_id))
            .order_by(SimulationRecord.created_at.desc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars())

    async def list_for_persona(self, persona_id: str, limit: int = 20) -> list[SimulationRecord]:
        stmt = (
            select(SimulationRecord)
            .where(or_(SimulationRecord.persona_a_id == persona_id, SimulationRecord.persona_b_id == persona_id))
            .order_by(SimulationRecord.created_at.desc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars())
