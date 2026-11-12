"""본인 확인 피드백 카드와 카드당 단일 답변.

Revision ID: fbc20261112a
Revises: y5b6c7d8e9f0
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "fbc20261112a"
down_revision: str | Sequence[str] | None = "y5b6c7d8e9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feedback_card_candidates",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("context_question", sa.String(200), nullable=True),
        sa.Column("utterance", sa.String(300), nullable=False),
        sa.Column("question", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="READY"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("shown_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('READY', 'SHOWN', 'ANSWERED', 'EXPIRED')", name="ck_feedback_card_status"),
    )
    op.create_index("ix_feedback_card_user_status", "feedback_card_candidates", ["user_id", "status"])
    op.create_index(
        "uq_feedback_card_user_shown",
        "feedback_card_candidates",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'SHOWN'"),
        sqlite_where=sa.text("status = 'SHOWN'"),
    )
    op.create_table(
        "feedback_card_answers",
        sa.Column("card_id", sa.String(64), sa.ForeignKey("feedback_card_candidates.id"), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("answer", sa.String(3), nullable=False),
        sa.Column("correction_text", sa.Text(), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consent_version", sa.String(64), nullable=False),
        sa.CheckConstraint("answer IN ('YES', 'NO')", name="ck_feedback_answer_value"),
        sa.CheckConstraint("answer = 'NO' OR correction_text IS NULL", name="ck_feedback_answer_correction"),
    )


def downgrade() -> None:
    op.drop_table("feedback_card_answers")
    op.drop_index("uq_feedback_card_user_shown", table_name="feedback_card_candidates")
    op.drop_index("ix_feedback_card_user_status", table_name="feedback_card_candidates")
    op.drop_table("feedback_card_candidates")
