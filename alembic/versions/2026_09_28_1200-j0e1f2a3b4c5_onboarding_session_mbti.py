"""onboarding_sessions.mbti — 온보딩 시작 때 받는 MBTI

Revision ID: j0e1f2a3b4c5
Revises: 8b2315adbff3
Create Date: 2026-09-28 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "j0e1f2a3b4c5"
down_revision: str | Sequence[str] | None = "8b2315adbff3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """/confirm 이 MBTI 없이 와도 잃지 않도록 시작 요청에서 받아 세션에 둔다. 기존 세션은 NULL."""
    op.add_column("onboarding_sessions", sa.Column("mbti", sa.String(length=4), nullable=True))
    op.create_check_constraint(
        "ck_onboarding_sessions_mbti_valid",
        "onboarding_sessions",
        "mbti IS NULL OR mbti IN ('ENFJ', 'ENFP', 'ENTJ', 'ENTP', 'ESFJ', 'ESFP', 'ESTJ', 'ESTP', "
        "'INFJ', 'INFP', 'INTJ', 'INTP', 'ISFJ', 'ISFP', 'ISTJ', 'ISTP')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_onboarding_sessions_mbti_valid", "onboarding_sessions", type_="check")
    op.drop_column("onboarding_sessions", "mbti")
