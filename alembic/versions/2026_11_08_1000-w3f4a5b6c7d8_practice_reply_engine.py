"""practice reply engine — 연습대화 세션의 답변 모델(애착 9칸 / 메인 LLM)과 칸 이름

Revision ID: w3f4a5b6c7d8
Revises: v2e3f4a5b6c7
Create Date: 2026-11-08 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "w3f4a5b6c7d8"
down_revision: str | Sequence[str] | None = "v2e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 둘 다 NULL 허용 — 기존 세션은 NULL(메인 LLM)로 남는다
    op.add_column("practice_sessions", sa.Column("reply_engine", sa.String(length=8), nullable=True))
    op.add_column("practice_sessions", sa.Column("slm_cell", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("practice_sessions", "slm_cell")
    op.drop_column("practice_sessions", "reply_engine")
