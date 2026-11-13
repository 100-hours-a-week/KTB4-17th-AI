"""sentences champion tags — 태깅 학습 데이터용 태거 전체 출력 · 입력 질문

Revision ID: z6c7d8e9f0a1
Revises: fbc20261112a
Create Date: 2026-11-13 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "z6c7d8e9f0a1"
down_revision: str | Sequence[str] | None = "fbc20261112a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sentences", sa.Column("champion_tags", sa.JSON(), nullable=True))
    op.add_column("sentences", sa.Column("champion_model", sa.String(length=128), nullable=True))
    op.add_column("sentences", sa.Column("context_question", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sentences", "context_question")
    op.drop_column("sentences", "champion_model")
    op.drop_column("sentences", "champion_tags")
