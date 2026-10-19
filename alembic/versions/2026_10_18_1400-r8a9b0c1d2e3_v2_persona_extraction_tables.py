"""v2 persona extraction tables — v1 extraction_jobs / imported_utterances 와 같은 구조의 v2 전용 테이블

v1 기능을 v2 로 옮기는 첫 단계(복사본). v1 테이블은 건드리지 않는다.

Revision ID: r8a9b0c1d2e3
Revises: p6e7f8a9b0c1
Create Date: 2026-10-18 14:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "r8a9b0c1d2e3"
down_revision: str | Sequence[str] | None = "p6e7f8a9b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v2_extraction_jobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("persona_id", sa.String(length=32), nullable=True),
        sa.Column("analyzed_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("context_payload", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_v2_extraction_jobs_user_id"), "v2_extraction_jobs", ["user_id"], unique=False)
    op.create_index(
        "uq_v2_extraction_jobs_active_user",
        "v2_extraction_jobs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )

    op.create_table(
        "v2_imported_utterances",
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
        sa.UniqueConstraint(
            "user_id", "sent_at", "content_hash", "occurrence", name="uq_v2_imported_utterances_identity"
        ),
    )
    op.create_index(op.f("ix_v2_imported_utterances_user_id"), "v2_imported_utterances", ["user_id"], unique=False)
    op.create_index(op.f("ix_v2_imported_utterances_job_id"), "v2_imported_utterances", ["job_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_v2_imported_utterances_job_id"), table_name="v2_imported_utterances")
    op.drop_index(op.f("ix_v2_imported_utterances_user_id"), table_name="v2_imported_utterances")
    op.drop_table("v2_imported_utterances")
    op.drop_index("uq_v2_extraction_jobs_active_user", table_name="v2_extraction_jobs")
    op.drop_index(op.f("ix_v2_extraction_jobs_user_id"), table_name="v2_extraction_jobs")
    op.drop_table("v2_extraction_jobs")
