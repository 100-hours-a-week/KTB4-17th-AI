"""chat_end tables — 채팅 종료 초안과 최종 종료 메시지

Revision ID: m3b4c5d6e7f8
Revises: l2a3b4c5d6e7
Create Date: 2026-10-08 12:00:00

recent_messages 는 저장하지 않는다(API 설계 문서 저장 범위).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "m3b4c5d6e7f8"
down_revision: str | Sequence[str] | None = "l2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_end_drafts",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("room_id", sa.BigInteger(), nullable=False),
        sa.Column("delegation_id", sa.BigInteger(), nullable=False),
        sa.Column("requester_user_id", sa.String(length=64), nullable=False),
        sa.Column("end_type", sa.String(length=16), nullable=False),
        sa.Column("ending_messages", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_chat_end_drafts_room_id"), "chat_end_drafts", ["room_id"], unique=False)
    op.create_index(op.f("ix_chat_end_drafts_delegation_id"), "chat_end_drafts", ["delegation_id"], unique=False)
    op.create_index(
        op.f("ix_chat_end_drafts_requester_user_id"), "chat_end_drafts", ["requester_user_id"], unique=False
    )

    op.create_table(
        "chat_end_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("room_id", sa.BigInteger(), nullable=False),
        sa.Column("delegation_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("target_user_id", sa.String(length=64), nullable=False),
        sa.Column("end_type", sa.String(length=16), nullable=False),
        sa.Column("ending_messages", sa.JSON(), nullable=False),
        sa.Column("ai_response", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("end_turns", sa.Integer(), nullable=False),
        sa.Column("end_reason", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_chat_end_messages_room_id"), "chat_end_messages", ["room_id"], unique=False)
    op.create_index(op.f("ix_chat_end_messages_delegation_id"), "chat_end_messages", ["delegation_id"], unique=False)
    op.create_index(op.f("ix_chat_end_messages_user_id"), "chat_end_messages", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_chat_end_messages_user_id"), table_name="chat_end_messages")
    op.drop_index(op.f("ix_chat_end_messages_delegation_id"), table_name="chat_end_messages")
    op.drop_index(op.f("ix_chat_end_messages_room_id"), table_name="chat_end_messages")
    op.drop_table("chat_end_messages")
    op.drop_index(op.f("ix_chat_end_drafts_requester_user_id"), table_name="chat_end_drafts")
    op.drop_index(op.f("ix_chat_end_drafts_delegation_id"), table_name="chat_end_drafts")
    op.drop_index(op.f("ix_chat_end_drafts_room_id"), table_name="chat_end_drafts")
    op.drop_table("chat_end_drafts")
