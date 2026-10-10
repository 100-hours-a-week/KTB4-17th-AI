"""simulation migration tables

Revision ID: l2a3b4c5d6e7
Revises: k1f2a3b4c5d6
Create Date: 2026-10-05 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "l2a3b4c5d6e7"
down_revision: str | Sequence[str] | None = "k1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "simulation_migration_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("persona_a_id", sa.String(length=32), nullable=False),
        sa.Column("persona_b_id", sa.String(length=32), nullable=False),
        sa.Column("pair_key", sa.String(length=65), nullable=False),
        sa.Column("user_id_a", sa.String(length=64), nullable=False),
        sa.Column("user_id_b", sa.String(length=64), nullable=False),
        sa.Column("nickname_a", sa.String(length=20), nullable=False),
        sa.Column("nickname_b", sa.String(length=20), nullable=False),
        sa.Column("turns", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("narrative_source", sa.String(length=8), nullable=True),
        sa.Column("error_reason", sa.String(length=64), nullable=True),
        sa.Column("validation", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["persona_a_id"], ["personas.id"]),
        sa.ForeignKeyConstraint(["persona_b_id"], ["personas.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_simulation_migration_runs_persona_a_id"),
        "simulation_migration_runs",
        ["persona_a_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_simulation_migration_runs_persona_b_id"),
        "simulation_migration_runs",
        ["persona_b_id"],
        unique=False,
    )
    op.create_index(
        "uq_simulation_migration_runs_pair_active",
        "simulation_migration_runs",
        ["pair_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('running', 'reporting')"),
        sqlite_where=sa.text("status IN ('running', 'reporting')"),
    )
    op.create_index(
        "uq_simulation_migration_runs_user_a_active",
        "simulation_migration_runs",
        ["user_id_a"],
        unique=True,
        postgresql_where=sa.text("status IN ('running', 'reporting')"),
        sqlite_where=sa.text("status IN ('running', 'reporting')"),
    )

    op.create_table(
        "simulation_migration_utterances",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.String(length=1), nullable=False),
        sa.Column("text", sa.String(length=300), nullable=False),
        sa.Column("validation", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["simulation_migration_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "index", name="uq_simulation_migration_utterances_run_index"),
        sa.CheckConstraint(
            "(\"index\" % 2 = 0 AND speaker = 'a') OR (\"index\" % 2 = 1 AND speaker = 'b')",
            name="ck_simulation_migration_utterances_speaker_parity",
        ),
    )
    op.create_index(
        op.f("ix_simulation_migration_utterances_run_id"),
        "simulation_migration_utterances",
        ["run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_simulation_migration_utterances_run_id"), table_name="simulation_migration_utterances")
    op.drop_table("simulation_migration_utterances")
    op.drop_index("uq_simulation_migration_runs_user_a_active", table_name="simulation_migration_runs")
    op.drop_index("uq_simulation_migration_runs_pair_active", table_name="simulation_migration_runs")
    op.drop_index(op.f("ix_simulation_migration_runs_persona_b_id"), table_name="simulation_migration_runs")
    op.drop_index(op.f("ix_simulation_migration_runs_persona_a_id"), table_name="simulation_migration_runs")
    op.drop_table("simulation_migration_runs")
