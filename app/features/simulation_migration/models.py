"""DB 테이블 — 시뮬레이션 마이그레이션.

대사를 한 건씩 저장하는 영속 계층.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class SimulationMigrationRun(Base):
    __tablename__ = "simulation_migration_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)

    persona_a_id: Mapped[str] = mapped_column(ForeignKey("personas.id"), index=True)
    persona_b_id: Mapped[str] = mapped_column(ForeignKey("personas.id"), index=True)
    pair_key: Mapped[str] = mapped_column(String(65))
    user_id_a: Mapped[str] = mapped_column(String(64))
    user_id_b: Mapped[str] = mapped_column(String(64))
    nickname_a: Mapped[str] = mapped_column(String(20))
    nickname_b: Mapped[str] = mapped_column(String(20))

    turns: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="running")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    narrative_source: Mapped[str | None] = mapped_column(String(8), nullable=True, default=None)
    error_reason: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    validation: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        Index(
            "uq_simulation_migration_runs_pair_active",
            "pair_key",
            unique=True,
            postgresql_where=text("status IN ('running', 'reporting')"),
            sqlite_where=text("status IN ('running', 'reporting')"),
        ),
        Index(
            "uq_simulation_migration_runs_user_a_active",
            "user_id_a",
            unique=True,
            postgresql_where=text("status IN ('running', 'reporting')"),
            sqlite_where=text("status IN ('running', 'reporting')"),
        ),
    )


class SimulationMigrationUtterance(Base):
    __tablename__ = "simulation_migration_utterances"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("simulation_migration_runs.id", ondelete="CASCADE"),
        index=True,
    )
    index: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String(1))
    text: Mapped[str] = mapped_column(String(300))
    validation: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        UniqueConstraint("run_id", "index", name="uq_simulation_migration_utterances_run_index"),
        CheckConstraint(
            "(\"index\" % 2 = 0 AND speaker = 'a') OR (\"index\" % 2 = 1 AND speaker = 'b')",
            name="ck_simulation_migration_utterances_speaker_parity",
        ),
    )


SimulationMigrationRunRecord = SimulationMigrationRun
SimulationMigrationUtteranceRecord = SimulationMigrationUtterance
