"""chat_end request_id and ai_turn_count — 멱등 재호출을 위한 request_id 및 턴 번호 컬럼

Revision ID: u1d2e3f4a5b6
Revises: t0c1d2e3f4a5
Create Date: 2026-10-20 10:00:00

chat_end_drafts   : request_id String(36) nullable, unique index
chat_end_messages : request_id String(36) nullable, unique index (SUCCESS 행에만 채운다)
                    ai_turn_count Integer nullable
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "u1d2e3f4a5b6"
down_revision: str | Sequence[str] | None = "t0c1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_end_drafts", sa.Column("request_id", sa.String(length=36), nullable=True))
    op.create_index(op.f("ix_chat_end_drafts_request_id"), "chat_end_drafts", ["request_id"], unique=True)

    op.add_column("chat_end_messages", sa.Column("request_id", sa.String(length=36), nullable=True))
    op.add_column("chat_end_messages", sa.Column("ai_turn_count", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_chat_end_messages_request_id"), "chat_end_messages", ["request_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_chat_end_messages_request_id"), table_name="chat_end_messages")
    op.drop_column("chat_end_messages", "ai_turn_count")
    op.drop_column("chat_end_messages", "request_id")

    op.drop_index(op.f("ix_chat_end_drafts_request_id"), table_name="chat_end_drafts")
    op.drop_column("chat_end_drafts", "request_id")
