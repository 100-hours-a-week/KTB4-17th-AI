"""v2 simulation tables — 하네스 시뮬레이션 비동기 작업(v2_simulation_jobs)과 결과(v2_simulations)

docs/기획_및_설계/시뮬레이션_v2_하네스_기획안_최종.md 5.9절. v1 simulations 테이블은 건드리지 않는다.

Revision ID: t0c1d2e3f4a5
Revises: s9b0c1d2e3f4
Create Date: 2026-10-19 11:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "t0c1d2e3f4a5"
down_revision: str | Sequence[str] | None = "s9b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "v2_simulation_jobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("me_user_id", sa.String(length=64), nullable=False),
        sa.Column("partner_key", sa.String(length=64), nullable=False),
        sa.Column("partner_persona_id", sa.String(length=32), nullable=False),
        sa.Column("turns", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=True),
        sa.Column("simulation_id", sa.String(length=32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_v2_simulation_jobs_me_user_id"), "v2_simulation_jobs", ["me_user_id"], unique=False)
    op.create_index(
        "uq_v2_simulation_jobs_active_pair",
        "v2_simulation_jobs",
        ["me_user_id", "partner_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )

    op.create_table(
        "v2_simulations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("me_user_id", sa.String(length=64), nullable=False),
        sa.Column("partner_user_id", sa.String(length=64), nullable=True),
        sa.Column("persona_a_id", sa.String(length=32), nullable=False),
        sa.Column("persona_b_id", sa.String(length=32), nullable=False),
        sa.Column("nickname_a", sa.String(length=20), nullable=False),
        sa.Column("nickname_b", sa.String(length=20), nullable=False),
        sa.Column("turns", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("transcript", _JSON, nullable=False),
        sa.Column("report", _JSON, nullable=False),
        sa.Column("topics_used", _JSON, nullable=False),
        sa.Column("quality_warnings", _JSON, nullable=False),
        sa.Column("harness_trace", _JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_v2_simulations_me_user_id"), "v2_simulations", ["me_user_id"], unique=False)
    op.create_index(op.f("ix_v2_simulations_partner_user_id"), "v2_simulations", ["partner_user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_v2_simulations_partner_user_id"), table_name="v2_simulations")
    op.drop_index(op.f("ix_v2_simulations_me_user_id"), table_name="v2_simulations")
    op.drop_table("v2_simulations")
    op.drop_index("uq_v2_simulation_jobs_active_pair", table_name="v2_simulation_jobs")
    op.drop_index(op.f("ix_v2_simulation_jobs_me_user_id"), table_name="v2_simulation_jobs")
    op.drop_table("v2_simulation_jobs")
