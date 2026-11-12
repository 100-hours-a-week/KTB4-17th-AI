"""sentence cards — 문장 판정 카드 (문장 · 유저별 노출 · 답변 묶음 수신 기록)

Revision ID: x4a5b6c7d8e9
Revises: w3f4a5b6c7d8
Create Date: 2026-11-11 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "x4a5b6c7d8e9"
down_revision: str | Sequence[str] | None = "w3f4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sentences",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("family_id", sa.String(length=64), nullable=False),
        sa.Column("sentence", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("question_type", sa.String(length=10), nullable=False),
        sa.Column("dimension", sa.String(length=32), nullable=False),
        sa.Column("expected_champion", sa.String(length=16), nullable=True),
        sa.Column("expected_teacher", sa.String(length=16), nullable=True),
        sa.Column("is_check", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("check_answer", sa.String(length=16), nullable=True),
        sa.Column("consensus", sa.String(length=16), nullable=True),
        sa.Column("verdict", sa.String(length=10), server_default="PENDING", nullable=False),
        sa.Column("agreement", sa.Float(), nullable=True),
        sa.Column("matches_teacher", sa.Boolean(), nullable=True),
        sa.Column("reviewed_by", sa.String(length=64), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sentences_family_id", "sentences", ["family_id"])

    op.create_table(
        "sentence_cards",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("sentence_id", sa.String(length=32), sa.ForeignKey("sentences.id"), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("shown_request_id", sa.String(length=36), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("choices", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=10), server_default="SHOWN", nullable=False),
        sa.Column("choice", sa.String(length=16), nullable=True),
        sa.Column("batch_id", sa.String(length=36), nullable=True),
        sa.Column("shown_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("sentence_id", "user_id", name="uq_sentence_cards_sentence_user"),
        sa.UniqueConstraint("shown_request_id", name="uq_sentence_cards_shown_request_id"),
    )
    op.create_index("ix_sentence_cards_sentence_id", "sentence_cards", ["sentence_id"])
    op.create_index("ix_sentence_cards_user_id", "sentence_cards", ["user_id"])
    op.create_index("ix_sentence_cards_batch_id", "sentence_cards", ["batch_id"])
    op.create_index("ix_sentence_cards_shown_at", "sentence_cards", ["shown_at"])

    op.create_table(
        "sentence_answer_batches",
        sa.Column("batch_id", sa.String(length=36), primary_key=True),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("sentence_answer_batches")
    op.drop_index("ix_sentence_cards_shown_at", table_name="sentence_cards")
    op.drop_index("ix_sentence_cards_batch_id", table_name="sentence_cards")
    op.drop_index("ix_sentence_cards_user_id", table_name="sentence_cards")
    op.drop_index("ix_sentence_cards_sentence_id", table_name="sentence_cards")
    op.drop_table("sentence_cards")
    op.drop_index("ix_sentences_family_id", table_name="sentences")
    op.drop_table("sentences")
