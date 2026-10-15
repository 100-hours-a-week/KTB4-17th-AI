"""시뮬레이션 마이그레이션 서비스 계층."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.features.persona.schemas import PersonaRef
from app.features.simulation_migration.manager import ConflictError, RunManager
from app.features.simulation_migration.repository import MigrationRepository
from app.features.simulation_migration.schemas import (
    MigrationListItem,
    MigrationRunView,
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
