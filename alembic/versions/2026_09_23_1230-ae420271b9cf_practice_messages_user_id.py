"""add user_id to practice_messages

practice_sessions.user_id를 메시지마다 복사해 세션 조인 없이 사용자별 메시지를
조회할 수 있게 한다. 세션의 사용자는 선택값이므로 nullable로 둔다.

Revision ID: ae420271b9cf
Revises: eb4cf6e3606d
Create Date: 2026-09-23 12:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ae420271b9cf"
down_revision: str | Sequence[str] | None = "eb4cf6e3606d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("practice_messages", sa.Column("user_id", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_practice_messages_user_id"), "practice_messages", ["user_id"], unique=False)
    op.execute(
        """
        UPDATE practice_messages AS message
        SET user_id = session.user_id
        FROM practice_sessions AS session
        WHERE message.session_id = session.id
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_practice_messages_user_id"), table_name="practice_messages")
    op.drop_column("practice_messages", "user_id")
