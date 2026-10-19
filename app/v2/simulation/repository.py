"""DB 접근. service.py 는 SQLAlchemy 쿼리를 직접 만들지 않는다."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.v2.persona.models import UserPersonaCore

from .models import ACTIVE_STATUSES, V2Simulation, V2SimulationJob


class SimulationV2Repository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── 작업 ──────────────────────────────────────────────

    # 진행 중 작업이 이미 있으면 유니크 인덱스가 flush 에서 IntegrityError 를 낸다
    async def create_job(
        self, *, me_user_id: str, partner_key: str, partner_persona_id: str, turns: int
    ) -> V2SimulationJob:
        job = V2SimulationJob(
            me_user_id=me_user_id,
            partner_key=partner_key,
            partner_persona_id=partner_persona_id,
            turns=turns,
            status="pending",
        )
        self.db.add(job)
        await self.db.flush()
        return job

    async def get_job(self, job_id: str) -> V2SimulationJob | None:
        return await self.db.get(V2SimulationJob, job_id)

    async def active_job(self, me_user_id: str, partner_key: str) -> V2SimulationJob | None:
        stmt = select(V2SimulationJob).where(
            V2SimulationJob.me_user_id == me_user_id,
            V2SimulationJob.partner_key == partner_key,
            V2SimulationJob.status.in_(ACTIVE_STATUSES),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    # ── 결과 ──────────────────────────────────────────────

    async def save(self, sim: V2Simulation) -> V2Simulation:
        self.db.add(sim)
        await self.db.flush()
        return sim

    async def get(self, simulation_id: str) -> V2Simulation | None:
        return await self.db.get(V2Simulation, simulation_id)

    # 내가 요청자든 상대든 다 나온다. 최신순
    async def list_for_user(self, user_id: str, *, limit: int = 50) -> list[V2Simulation]:
        stmt = (
            select(V2Simulation)
            .where(or_(V2Simulation.me_user_id == user_id, V2Simulation.partner_user_id == user_id))
            .order_by(V2Simulation.created_at.desc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars())

    # ── v2 코어 프로필 ────────────────────────────────────

    async def core_profile(self, user_id: str | None) -> dict:
        if not user_id:
            return {}
        core = await self.db.get(UserPersonaCore, user_id)
        return dict(core.profile) if core and core.profile else {}
