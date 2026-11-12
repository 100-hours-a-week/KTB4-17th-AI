"""sentence training triggers — 문장 판정 카드 학습 후보 알림 기록

Revision ID: y5b6c7d8e9f0
Revises: x4a5b6c7d8e9
Create Date: 2026-11-11 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "y5b6c7d8e9f0"
down_revision: str | Sequence[str] | None = "x4a5b6c7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sentence_training_triggers",
        sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("agree", sa.Integer(), nullable=False),
        sa.Column("disagree_reviewed", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("sentence_training_triggers")
