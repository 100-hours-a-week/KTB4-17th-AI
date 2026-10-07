"""시뮬레이션 마이그레이션 DB 접근 계층."""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import SimulationMigrationRun, SimulationMigrationUtterance


def _now() -> datetime:
    return datetime.now(UTC)


def pair_key(persona_a_id: str, persona_b_id: str) -> str:
    """페르소나 ID 쌍의 고유 키를 반환한다.

    항상 min:max 순서로 결합하여 화자 순서와 무관하게 같은 쌍을 식별한다.
    """
    if persona_a_id < persona_b_id:
        return f"{persona_a_id}:{persona_b_id}"
    return f"{persona_b_id}:{persona_a_id}"


class MigrationRepository:
    """시뮬레이션 마이그레이션 영속화 리포지토리.

    메서드는 flush만 하고 commit하지 않는다. 세션 오류는 호출자가 본다.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def insert_run(
        self,
        *,
        persona_a_id: str,
        persona_b_id: str,
        user_id_a: str,
        user_id_b: str,
        nickname_a: str,
        nickname_b: str,
        turns: int,
        id: str | None = None,
        report: dict | None = None,
        narrative_source: str | None = None,
        error_reason: str | None = None,
        validation: dict | None = None,
    ) -> SimulationMigrationRun:
        """새 시뮬레이션 실행 행을 생성한다.

        status는 running, attempt는 1, heartbeat_at과 updated_at은 now.
        """
        now = _now()
        kwargs: dict[str, Any] = {
            "persona_a_id": persona_a_id,
            "persona_b_id": persona_b_id,
            "pair_key": pair_key(persona_a_id, persona_b_id),
            "user_id_a": user_id_a,
            "user_id_b": user_id_b,
            "nickname_a": nickname_a,
            "nickname_b": nickname_b,
            "turns": turns,
            "status": "running",
            "attempt": 1,
            "report": report,
            "narrative_source": narrative_source,
            "error_reason": error_reason,
            "validation": validation,
            "created_at": now,
            "updated_at": now,
            "heartbeat_at": now,
        }
        if id is not None:
            kwargs["id"] = id
        run = SimulationMigrationRun(**kwargs)
        self.db.add(run)
        await self.db.flush()
        return run

    async def get_run(self, run_id: str) -> SimulationMigrationRun | None:
        """run_id로 실행 행을 조회한다."""
        return await self.db.get(SimulationMigrationRun, run_id)

    async def list_utterances(self, run_id: str) -> list[SimulationMigrationUtterance]:
        """발화 행을 index 오름차순으로 조회한다."""
        stmt = (
            select(SimulationMigrationUtterance)
            .where(SimulationMigrationUtterance.run_id == run_id)
            .order_by(SimulationMigrationUtterance.index.asc())
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def conditional_update(
        self,
        run_id: str,
        attempt: int,
        from_statuses: Collection[str],
        /,
        **fields: Any,
    ) -> bool:
        """소유권을 검증하며 실행 행의 상태 및 필드를 조건부 갱신한다.

        UPDATE는 WHERE id=:id AND attempt=:attempt AND status IN from_statuses.
        rowcount가 1일 때만 True. 항상 updated_at을 now로 같이 쓴다.
        0행이면 False이고 다른 컬럼은 그대로다.
        """
        now = _now()
        values = {**fields, "updated_at": now}
        stmt = (
            update(SimulationMigrationRun)
            .where(
                SimulationMigrationRun.id == run_id,
                SimulationMigrationRun.attempt == attempt,
                SimulationMigrationRun.status.in_(from_statuses),
            )
            .values(**values)
        )
        res = await self.db.execute(stmt)
        await self.db.flush()
        return res.rowcount == 1

    async def insert_utterance_if_owner(
        self,
        run_id: str,
        attempt: int,
        index: int,
        speaker: str,
        text: str,
        validation: dict | None = None,
    ) -> Literal["inserted", "lost", "exists"]:
        """소유권을 확인하고 발화를 저장한다.

        같은 트랜잭션에서 먼저 UPDATE heartbeat_at=now, updated_at=now WHERE id AND attempt AND status='running'.
        rowcount가 1이 아니면 insert 없이 'lost'.
        그 다음 insert. (run_id, index)가 이미 있으면 status와 error_reason을 쓰지 않고 'exists'.
        unique 위반이면 savepoint를 롤백해 heartbeat 갱신도 되돌리고, status와 error_reason은 쓰지 않는다.
        롤백 뒤 그 index 행이 있고 attempt가 아직 자신이면 'exists', 아니면 'lost'.
        """
        now = _now()
        async with self.db.begin_nested() as sp:
            stmt = (
                update(SimulationMigrationRun)
                .where(
                    SimulationMigrationRun.id == run_id,
                    SimulationMigrationRun.attempt == attempt,
                    SimulationMigrationRun.status == "running",
                )
                .values(heartbeat_at=now, updated_at=now)
            )
            res = await self.db.execute(stmt)
            if res.rowcount != 1:
                await sp.rollback()
                return "lost"

            utterance = SimulationMigrationUtterance(
                run_id=run_id,
                index=index,
                speaker=speaker,
                text=text,
                validation=validation,
                created_at=now,
            )
            self.db.add(utterance)
            try:
                await self.db.flush()
                return "inserted"
            except IntegrityError as exc:
                err_text = str(exc).lower()
                # CheckConstraint 등 speaker parity 위반은 예외를 전파한다
                is_parity_violation = (index % 2 == 0 and speaker != "a") or (index % 2 == 1 and speaker != "b")
                if is_parity_violation or "check" in err_text or "ck_" in err_text:
                    await sp.rollback()
                    raise
                await sp.rollback()

        # 롤백 뒤 그 index 행이 있고 attempt가 아직 자신이면 "exists", 아니면 "lost"
        run = await self.get_run(run_id)
        if run is not None and run.attempt == attempt:
            stmt = select(SimulationMigrationUtterance.id).where(
                SimulationMigrationUtterance.run_id == run_id,
                SimulationMigrationUtterance.index == index,
            )
            existing = (await self.db.execute(stmt)).scalar_one_or_none()
            if existing is not None:
                return "exists"
        return "lost"

    async def abort_stale(
        self,
        run_id: str,
        *,
        stale_before: datetime,
        attempt: int | None = None,
    ) -> bool:
        """오래된 미완료 실행을 aborted 상태로 조건부 갱신한다.

        status를 aborted, error_reason을 aborted로 조건부 갱신.
        WHERE status IN ('running','reporting') AND heartbeat_at < stale_before.
        attempt가 있으면 attempt도 조건에 넣는다. fresh 행은 그대로다.
        """
        now = _now()
        conditions = [
            SimulationMigrationRun.id == run_id,
            SimulationMigrationRun.status.in_(["running", "reporting"]),
            SimulationMigrationRun.heartbeat_at < stale_before,
        ]
        if attempt is not None:
            conditions.append(SimulationMigrationRun.attempt == attempt)

        stmt = (
            update(SimulationMigrationRun)
            .where(*conditions)
            .values(
                status="aborted",
                error_reason="aborted",
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        res = await self.db.execute(stmt)
        await self.db.flush()
        if res.rowcount == 1:
            self.db.expire_all()
        return res.rowcount == 1

    async def find_active_by_pair(self, pair_key: str) -> SimulationMigrationRun | None:
        """pair_key로 활성(running 또는 reporting) 실행을 조회한다."""
        stmt = (
            select(SimulationMigrationRun)
            .where(
                SimulationMigrationRun.pair_key == pair_key,
                SimulationMigrationRun.status.in_(["running", "reporting"]),
            )
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalars().first()

    async def list_for_user(self, user_id: str, limit: int = 20) -> list[SimulationMigrationRun]:
        """사용자가 a 또는 b인 시뮬레이션 목록을 최신순으로 조회한다."""
        stmt = (
            select(SimulationMigrationRun)
            .where(
                or_(
                    SimulationMigrationRun.user_id_a == user_id,
                    SimulationMigrationRun.user_id_b == user_id,
                )
            )
            .order_by(SimulationMigrationRun.created_at.desc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def list_for_persona(self, persona_id: str, limit: int = 20) -> list[SimulationMigrationRun]:
        """페르소나가 a 또는 b인 시뮬레이션 목록을 최신순으로 조회한다."""
        stmt = (
            select(SimulationMigrationRun)
            .where(
                or_(
                    SimulationMigrationRun.persona_a_id == persona_id,
                    SimulationMigrationRun.persona_b_id == persona_id,
                )
            )
            .order_by(SimulationMigrationRun.created_at.desc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars().all())
