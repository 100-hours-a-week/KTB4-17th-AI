"""require user_id (onboarding_sessions, personas, simulations)

user_id 가 이제 온보딩 시작부터 필수라, 기존에 nullable 이던 컬럼들을 NOT NULL 로 바꾼다.
과거에 만들어진 행 중 user_id 가 비어 있는 게 있으면 먼저 "unknown" 으로 채운 뒤 제약을 건다 —
그런 행은 프론트가 애초에 로그인 없이 만든 테스트 데이터라 실제 서비스에는 없을 것으로 본다.

Revision ID: eb4cf6e3606d
Revises: b3f1c2d4e5a6
Create Date: 2026-09-23 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "eb4cf6e3606d"
down_revision: str | Sequence[str] | None = "b3f1c2d4e5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLACEHOLDER = "unknown"


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(f"UPDATE onboarding_sessions SET user_id = '{PLACEHOLDER}' WHERE user_id IS NULL")
    op.execute(f"UPDATE personas SET user_id = '{PLACEHOLDER}' WHERE user_id IS NULL")
    op.execute(f"UPDATE simulations SET user_id_a = '{PLACEHOLDER}' WHERE user_id_a IS NULL")
    op.execute(f"UPDATE simulations SET user_id_b = '{PLACEHOLDER}' WHERE user_id_b IS NULL")

    op.alter_column("onboarding_sessions", "user_id", existing_type=sa.String(length=64), nullable=False)
    op.alter_column("personas", "user_id", existing_type=sa.String(length=64), nullable=False)
    op.alter_column("simulations", "user_id_a", existing_type=sa.String(length=64), nullable=False)
    op.alter_column("simulations", "user_id_b", existing_type=sa.String(length=64), nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column("simulations", "user_id_b", existing_type=sa.String(length=64), nullable=True)
    op.alter_column("simulations", "user_id_a", existing_type=sa.String(length=64), nullable=True)
    op.alter_column("personas", "user_id", existing_type=sa.String(length=64), nullable=True)
    op.alter_column("onboarding_sessions", "user_id", existing_type=sa.String(length=64), nullable=True)
