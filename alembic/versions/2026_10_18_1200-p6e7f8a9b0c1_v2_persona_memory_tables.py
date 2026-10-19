"""v2 persona memory tables — 코어 프로필 / 세부 사실(pgvector) / 메모리 갱신 작업

Revision ID: p6e7f8a9b0c1
Revises: o5d6e7f8a9b0
Create Date: 2026-10-18 12:00:00

user_persona_core   : 유저당 1행 코어 프로필(JSONB), 추출 커서, 마지막 활동 시각
user_persona_fact   : 세부 사실 + embedding vector(1024). user_id/status, user_id/category 복합 인덱스
memory_update_jobs  : 백그라운드 갱신 작업. 유저당 pending/running 1개만 허용하는 partial unique 인덱스
vector 확장이 필요하다(CREATE EXTENSION IF NOT EXISTS). downgrade 는 확장을 지우지 않는다.
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p6e7f8a9b0c1"
down_revision: str | Sequence[str] | None = "o5d6e7f8a9b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "user_persona_core",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("profile", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_processed_message_id", sa.Integer(), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "user_persona_fact",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=True),
        sa.Column("fact", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(dim=1024), nullable=True),
        sa.Column("embedding_model", sa.String(length=64), nullable=True),
        sa.Column("source_type", sa.String(length=16), nullable=True),
        sa.Column("source_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_user_persona_fact_user_status", "user_persona_fact", ["user_id", "status"], unique=False)
    op.create_index("idx_user_persona_fact_user_category", "user_persona_fact", ["user_id", "category"], unique=False)

    op.create_table(
        "memory_update_jobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("trigger_reason", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_memory_update_jobs_user_id"), "memory_update_jobs", ["user_id"], unique=False)
    op.create_index(
        "uq_memory_update_jobs_active_user",
        "memory_update_jobs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
        sqlite_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_memory_update_jobs_active_user",
        table_name="memory_update_jobs",
        postgresql_where=sa.text("status IN ('pending', 'running')"),
        sqlite_where=sa.text("status IN ('pending', 'running')"),
    )
    op.drop_index(op.f("ix_memory_update_jobs_user_id"), table_name="memory_update_jobs")
    op.drop_table("memory_update_jobs")

    op.drop_index("idx_user_persona_fact_user_category", table_name="user_persona_fact")
    op.drop_index("idx_user_persona_fact_user_status", table_name="user_persona_fact")
    op.drop_table("user_persona_fact")

    op.drop_table("user_persona_core")
