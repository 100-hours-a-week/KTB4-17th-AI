"""onboarding_turns (session_id, turn_index) unique

Revision ID: i9d0e1f2a3b4
Revises: h8c9d0e1f2a3
Create Date: 2026-09-28 11:00:00

배포 전 중복 확인:
  SELECT session_id, turn_index, COUNT(*) FROM onboarding_turns GROUP BY 1, 2 HAVING COUNT(*) > 1;
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "i9d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "h8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """같은 세션의 같은 턴에 질문 행이 둘 생기지 않게 한다 (동시 답변 요청의 마지막 안전장치)."""
    op.create_unique_constraint(
        "uq_onboarding_turns_session_turn",
        "onboarding_turns",
        ["session_id", "turn_index"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_onboarding_turns_session_turn", "onboarding_turns", type_="unique")
