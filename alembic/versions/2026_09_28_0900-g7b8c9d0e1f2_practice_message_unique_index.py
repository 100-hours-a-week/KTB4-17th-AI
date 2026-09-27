"""practice_messages (session_id, index) unique

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-09-28 09:00:00

배포 전 중복 확인:
  SELECT session_id, index, COUNT(*) FROM practice_messages GROUP BY 1, 2 HAVING COUNT(*) > 1;
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "g7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_practice_messages_session_index", "practice_messages", ["session_id", "index"])


def downgrade() -> None:
    op.drop_constraint("uq_practice_messages_session_index", "practice_messages", type_="unique")
