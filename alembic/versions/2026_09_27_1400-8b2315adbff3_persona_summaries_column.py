"""personas.summaries column — area별 요약 카드

Revision ID: 8b2315adbff3
Revises: i9d0e1f2a3b4
Create Date: 2026-09-27 14:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8b2315adbff3"
down_revision: str | Sequence[str] | None = "i9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("personas", sa.Column("summaries", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("personas", "summaries")
