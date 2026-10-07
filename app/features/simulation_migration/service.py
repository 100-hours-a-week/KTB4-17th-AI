"""시뮬레이션 마이그레이션 서비스 계층."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.features.persona.lookup import load_persona
from app.features.persona.schemas import PersonaRef
from app.features.simulation_migration.manager import ConflictError, RunManager, SlotUnavailable
from app.features.simulation_migration.repository import MigrationRepository, pair_key
from app.features.simulation_migration.schemas import (
    MigrationListItem,
    MigrationRunView,
    MigrationStartRequest,
    MigrationUtteranceView,
)


class PersonaNotFound(Exception):
    """페르소나를 찾을 수 없음."""

    def __init__(self, who: str, ref: PersonaRef) -> None:
        self.who = who
        self.ref = ref
        super().__init__(f"{who}: persona not found for {ref.describe()}")


class SimulationNotFound(Exception):
    """시뮬레이션 실행 행 미존재."""


class SimulationAlreadyRunning(Exception):
    """동일 페르소나 쌍이 이미 진행 중임."""


class SimulationMigrationService:
    """시뮬레이션 마이그레이션 서비스."""

    def __init__(self, db: AsyncSession, manager: RunManager | None = None) -> None:
        self.db = db
        self.repo = MigrationRepository(db)
        self.manager = manager
        self.settings = get_settings()

    async def start_simulation(self, req: MigrationStartRequest) -> str:
        """새 시뮬레이션을 생성하고 비동기 task를 시작한다."""
        if self.manager is None:
            raise RuntimeError("RunManager가 설정되지 않았습니다.")

        # 1. 페르소나 로드
        loaded_a = await load_persona(self.db, PersonaRef(user_id=req.me_user_id))
        if loaded_a is None:
            raise PersonaNotFound("me", PersonaRef(user_id=req.me_user_id))

        partner_ref = PersonaRef(user_id=req.partner_user_id)
        loaded_b = await load_persona(self.db, partner_ref)
        if loaded_b is None:
            raise PersonaNotFound("partner", partner_ref)

        persona_a = loaded_a.response
        persona_b = loaded_b.response

        # 2. 동일 pair_key 활성 여부 검증
        p_key = pair_key(persona_a.persona_id, persona_b.persona_id)
        active_run = await self.repo.find_active_by_pair(p_key)
        if active_run is not None:
            raise SimulationAlreadyRunning(f"Pair {p_key} is already active with status {active_run.status}")

        # 3. 프로세스 슬롯 획득
        if not self.manager.acquire_run_slot():
            raise SlotUnavailable(f"최대 동시 실행 수({self.manager.max_runs})에 도달했습니다.")

        # 4. RunManager를 통해 시작 (insert 실패 시 슬롯 반환 처리 포함)
        task = await self.manager.start_run(
            persona_a=persona_a,
            persona_b=persona_b,
            user_id_a=req.me_user_id,
            user_id_b=req.partner_user_id,
            nickname_a=loaded_a.nickname,
            nickname_b=loaded_b.nickname,
            turns=req.turns,
            slot_acquired=True,
        )
        return task.run_id

    async def resume_simulation(self, simulation_id: str, me_user_id: str | None = None) -> str:
        """중단되었거나 실패한 시뮬레이션을 재개한다."""
        if self.manager is None:
            raise RuntimeError("RunManager가 설정되지 않았습니다.")

        run = await self.repo.get_run(simulation_id)
        if run is None:
            raise SimulationNotFound(f"Simulation {simulation_id} not found")

        if me_user_id is None or run.user_id_a != me_user_id:
            raise SimulationNotFound("요청자와 시뮬레이션 소유자가 일치하지 않습니다.")

        if run.status not in ("failed", "aborted"):
            raise ConflictError(f"Simulation {simulation_id} status is {run.status}, cannot resume")

        await self.manager.resume(simulation_id)
        return simulation_id

    async def get_simulation(self, simulation_id: str) -> MigrationRunView:
        """단건 시뮬레이션 상태를 조회한다 (stale 상태 표시 계산 포함)."""
        run = await self.repo.get_run(simulation_id)
        if run is None:
            raise SimulationNotFound(f"Simulation {simulation_id} not found")

        status = run.status
        now = datetime.now(UTC)
        stale_threshold = now - timedelta(seconds=self.settings.simulation_migration_stale_s)
        if status in ("running", "reporting") and run.heartbeat_at < stale_threshold:
            status = "aborted"

        utterances = await self.repo.list_utterances(simulation_id)
        views = [
            MigrationUtteranceView(
                index=u.index,
                speaker=u.speaker,  # type: ignore[arg-type]
                nickname=run.nickname_a if u.speaker == "a" else run.nickname_b,
                text=u.text,
            )
            for u in utterances
        ]

        return MigrationRunView(
            simulation_id=run.id,
            status=status,  # type: ignore[arg-type]
            turns=run.turns,
            attempt=run.attempt,
            error_reason=run.error_reason,
            narrative_source=run.narrative_source,  # type: ignore[arg-type]
            utterances=views,
        )

    async def list_for_user(self, user_id: str, limit: int = 20) -> list[MigrationListItem]:
        """사용자가 참여한 시뮬레이션 목록을 반환한다."""
        runs = await self.repo.list_for_user(user_id, limit=limit)
        now = datetime.now(UTC)
        stale_threshold = now - timedelta(seconds=self.settings.simulation_migration_stale_s)

        items: list[MigrationListItem] = []
        for r in runs:
            status = r.status
            if status in ("running", "reporting") and r.heartbeat_at < stale_threshold:
                status = "aborted"
            items.append(
                MigrationListItem(
                    simulation_id=r.id,
                    status=status,  # type: ignore[arg-type]
                    turns=r.turns,
                    created_at=r.created_at,
                )
            )
        return items

    async def list_for_persona(self, persona_id: str, limit: int = 20) -> list[MigrationListItem]:
        """페르소나가 참여한 시뮬레이션 목록을 반환한다."""
        runs = await self.repo.list_for_persona(persona_id, limit=limit)
        now = datetime.now(UTC)
        stale_threshold = now - timedelta(seconds=self.settings.simulation_migration_stale_s)

        items: list[MigrationListItem] = []
        for r in runs:
            status = r.status
            if status in ("running", "reporting") and r.heartbeat_at < stale_threshold:
                status = "aborted"
            items.append(
                MigrationListItem(
                    simulation_id=r.id,
                    status=status,  # type: ignore[arg-type]
                    turns=r.turns,
                    created_at=r.created_at,
                )
            )
        return items


MigrationService = SimulationMigrationService
