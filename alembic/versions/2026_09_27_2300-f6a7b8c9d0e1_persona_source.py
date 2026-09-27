"""add persona extraction source (llm | fallback)

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-27 23:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """기존 페르소나는 전부 LLM 추출본이므로 llm 으로 채운다."""
    op.add_column(
        "personas",
        sa.Column("source", sa.String(length=8), server_default="llm", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("personas", "source")
