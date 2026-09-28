"""add persona confirmation state

Revision ID: d4e5f6a7b8c9
Revises: c7d8e9f0a1b2
Create Date: 2026-09-27 21:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """기존 페르소나는 확정본으로 보존하고 이후 /build 결과는 초안으로 만든다."""
    op.add_column(
        "personas",
        sa.Column("is_confirmed", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.execute("UPDATE personas SET is_confirmed = TRUE, confirmed_at = COALESCE(confirmed_at, created_at)")
    op.create_check_constraint(
        "ck_personas_confirmation_consistent",
        "personas",
        "(is_confirmed AND confirmed_at IS NOT NULL) OR (NOT is_confirmed AND confirmed_at IS NULL)",
    )
    op.create_unique_constraint(
        "uq_personas_session_version",
        "personas",
        ["session_id", "version"],
    )
    op.create_index(
        "ix_personas_user_id_is_confirmed",
        "personas",
        ["user_id", "is_confirmed"],
        unique=False,
    )


def downgrade() -> None:
    """확정 상태만 제거하고 기존 confirmed_at 값은 보존한다."""
    op.drop_index("ix_personas_user_id_is_confirmed", table_name="personas")
    op.drop_constraint("uq_personas_session_version", "personas", type_="unique")
    op.drop_constraint("ck_personas_confirmation_consistent", "personas", type_="check")
    op.drop_column("personas", "is_confirmed")
