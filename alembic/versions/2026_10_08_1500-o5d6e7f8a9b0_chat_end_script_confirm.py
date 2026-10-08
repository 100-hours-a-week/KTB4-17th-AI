"""chat_end script + confirm — 초안을 턴별 스크립트로, 확정 상태 추가

Revision ID: o5d6e7f8a9b0
Revises: m3b4c5d6e7f8
Create Date: 2026-10-08 15:00:00

chat_end_drafts   : ending_messages → turns, status(PENDING/CONFIRMED), confirmed_at, 같은 방·위임 CONFIRMED 1개 인덱스
chat_end_messages : ending_messages 삭제, draft_id·direction 추가
기존 행은 모두 PENDING 이다(옛 3개 대안은 스크립트가 아니므로 messages 가 쓰지 못한다).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "o5d6e7f8a9b0"
down_revision: str | Sequence[str] | None = "n4c5d6e7f8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("chat_end_drafts", "ending_messages", new_column_name="turns")
    op.add_column(
        "chat_end_drafts",
        sa.Column("status", sa.String(length=10), nullable=False, server_default="PENDING"),
    )
    op.add_column("chat_end_drafts", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "uq_chat_end_drafts_confirmed",
        "chat_end_drafts",
        ["room_id", "delegation_id"],
        unique=True,
        postgresql_where=sa.text("status = 'CONFIRMED'"),
        sqlite_where=sa.text("status = 'CONFIRMED'"),
    )

    op.add_column("chat_end_messages", sa.Column("draft_id", sa.String(length=32), nullable=True))
    op.add_column("chat_end_messages", sa.Column("direction", sa.Text(), nullable=True))
    op.create_index(op.f("ix_chat_end_messages_draft_id"), "chat_end_messages", ["draft_id"], unique=False)
    op.drop_column("chat_end_messages", "ending_messages")


def downgrade() -> None:
    op.add_column(
        "chat_end_messages",
        sa.Column("ending_messages", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.alter_column("chat_end_messages", "ending_messages", server_default=None)
    op.drop_index(op.f("ix_chat_end_messages_draft_id"), table_name="chat_end_messages")
    op.drop_column("chat_end_messages", "direction")
    op.drop_column("chat_end_messages", "draft_id")

    op.drop_index("uq_chat_end_drafts_confirmed", table_name="chat_end_drafts")
    op.drop_column("chat_end_drafts", "confirmed_at")
    op.drop_column("chat_end_drafts", "status")
    op.alter_column("chat_end_drafts", "turns", new_column_name="ending_messages")
