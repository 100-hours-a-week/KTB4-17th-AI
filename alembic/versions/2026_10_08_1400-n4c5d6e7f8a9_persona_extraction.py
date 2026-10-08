"""persona extraction

실제 대화(연습대화·카카오톡) → 페르소나 대화 스타일.
- personas.conversation_style: 관찰한 말투·대화 습관
- practice_messages.reflected_persona_id: 이 발화가 반영된 페르소나 버전 (NULL = 미반영)
- practice_messages.user_id backfill: ae420271b9cf 이후 모델 누락으로 NULL 로 쌓인 행을 세션 값으로 채운다.
  세션 user_id 가 NULL 인 대화는 주인을 알 수 없어 그대로 둔다. 채운 행은 미반영이라 다음 추출 대상이 된다.
- extraction_jobs, imported_utterances: 추출 작업과 업로드 대화의 본인 발화

Revision ID: n4c5d6e7f8a9
Revises: m3b4c5d6e7f8
Create Date: 2026-10-08 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "n4c5d6e7f8a9"
down_revision: str | Sequence[str] | None = "m3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("personas", sa.Column("conversation_style", sa.JSON(), nullable=True))
    op.add_column("practice_messages", sa.Column("reflected_persona_id", sa.String(length=32), nullable=True))
    op.execute(
        """
        UPDATE practice_messages AS message
        SET user_id = session.user_id
        FROM practice_sessions AS session
        WHERE message.session_id = session.id
          AND message.user_id IS NULL
          AND session.user_id IS NOT NULL
        """
    )

    op.create_table(
        "extraction_jobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("persona_id", sa.String(length=32), nullable=True),
        sa.Column("analyzed_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_extraction_jobs_user_id"), "extraction_jobs", ["user_id"], unique=False)
    op.create_index(
        "uq_extraction_jobs_active_user",
        "extraction_jobs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )

    op.create_table(
        "imported_utterances",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("occurrence", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=False),
        sa.Column("reflected_persona_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "sent_at", "content_hash", "occurrence", name="uq_imported_utterances_identity"),
    )
    op.create_index(op.f("ix_imported_utterances_user_id"), "imported_utterances", ["user_id"], unique=False)
    op.create_index(op.f("ix_imported_utterances_job_id"), "imported_utterances", ["job_id"], unique=False)


def downgrade() -> None:
    # backfill 한 practice_messages.user_id 는 되돌리지 않는다 — 원래 채워졌어야 할 값이다
    op.drop_index(op.f("ix_imported_utterances_job_id"), table_name="imported_utterances")
    op.drop_index(op.f("ix_imported_utterances_user_id"), table_name="imported_utterances")
    op.drop_table("imported_utterances")
    op.drop_index("uq_extraction_jobs_active_user", table_name="extraction_jobs")
    op.drop_index(op.f("ix_extraction_jobs_user_id"), table_name="extraction_jobs")
    op.drop_table("extraction_jobs")
    op.drop_column("practice_messages", "reflected_persona_id")
    op.drop_column("personas", "conversation_style")
