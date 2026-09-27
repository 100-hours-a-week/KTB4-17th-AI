"""report_previews table — /report/preview 호출을 내부 확인용으로 저장

Revision ID: h8c9d0e1f2a3
Revises: f6a7b8c9d0e1
Create Date: 2026-09-28 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "h8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_previews",
        sa.Column("id", sa.String(length=32), primary_key=True),
        # 요청 본문을 그대로 — personas 테이블 FK 아님(임의 JSON일 수 있다)
        sa.Column("persona_a", sa.JSON(), nullable=False),
        sa.Column("persona_b", sa.JSON(), nullable=False),
        sa.Column("nickname_a", sa.String(length=64), nullable=False),
        sa.Column("nickname_b", sa.String(length=64), nullable=False),
        sa.Column("transcript", sa.JSON(), nullable=False),
        sa.Column("use_llm", sa.Boolean(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("narrative_source", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_report_previews_created_at"), "report_previews", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_report_previews_created_at"), table_name="report_previews")
    op.drop_table("report_previews")
