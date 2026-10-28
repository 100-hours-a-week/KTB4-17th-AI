"""drop v2 practice speech state — 말 놓기 판정 테이블과 거절 시각 컬럼 제거

Revision ID: v2e3f4a5b6c7
Revises: u1d2e3f4a5b6
Create Date: 2026-10-28 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v2e3f4a5b6c7"
down_revision: str | Sequence[str] | None = "u1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("user_persona_core", "casual_speech_declined_at")
    op.drop_table("v2_practice_speech_state")


def downgrade() -> None:
    op.create_table(
        "v2_practice_speech_state",
        sa.Column("session_id", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=12), nullable=False),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["practice_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.add_column(
        "user_persona_core", sa.Column("casual_speech_declined_at", sa.DateTime(timezone=True), nullable=True)
    )
