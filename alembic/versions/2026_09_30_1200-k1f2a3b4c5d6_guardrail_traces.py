"""Create guardrail audit table.

Revision ID: k1f2a3b4c5d6
Revises: j0e1f2a3b4c5
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "k1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "j0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "guardrail_traces",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("feature", sa.String(length=32), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("guardrail_status", sa.String(length=16), nullable=False),
        sa.Column("grade", sa.String(length=16), nullable=False),
        sa.Column("initial_grade", sa.String(length=16), nullable=False),
        sa.Column("violation_domains", sa.JSON(), nullable=False),
        sa.Column("initial_response_text", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("guardrail_traces")
