"""v2 practice speech state — 연습대화 말 놓기 제안 상태

- v2_practice_speech_state: 세션별 상태 (formal | asked | casual | declined)
- user_persona_core.casual_speech_declined_at: 사용자가 말 놓기를 거절한 시각 (있으면 다시 묻지 않음)

Revision ID: s9b0c1d2e3f4
Revises: r8a9b0c1d2e3
Create Date: 2026-10-19 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "s9b0c1d2e3f4"
down_revision: str | Sequence[str] | None = "r8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_column("user_persona_core", "casual_speech_declined_at")
    op.drop_table("v2_practice_speech_state")
