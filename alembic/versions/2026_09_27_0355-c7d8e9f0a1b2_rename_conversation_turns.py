"""rename conversation_turns to onboarding_turns

행을 복사하거나 테이블을 다시 만들지 않고 PostgreSQL 카탈로그의 이름만 바꿔
기존 온보딩 대화와 SERIAL 값을 그대로 보존한다.

Revision ID: c7d8e9f0a1b2
Revises: b2837148de20
Create Date: 2026-09-27 03:55:00
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: str | Sequence[str] | None = "b2837148de20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """온보딩 턴 테이블과 관련 DB 객체의 이름을 함께 변경한다."""
    # 활성 요청이 잡고 있는 잠금을 오래 기다리며 API를 막지 않고, 실패 시 전체 DDL을 롤백한다.
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.rename_table("conversation_turns", "onboarding_turns")
    op.execute("ALTER INDEX ix_conversation_turns_session_id RENAME TO ix_onboarding_turns_session_id")
    op.execute("ALTER TABLE onboarding_turns RENAME CONSTRAINT conversation_turns_pkey TO onboarding_turns_pkey")
    op.execute(
        "ALTER TABLE onboarding_turns RENAME CONSTRAINT conversation_turns_session_id_fkey "
        "TO onboarding_turns_session_id_fkey"
    )
    op.execute("ALTER SEQUENCE conversation_turns_id_seq RENAME TO onboarding_turns_id_seq")


def downgrade() -> None:
    """데이터를 유지한 채 모든 이름을 이전 상태로 되돌린다."""
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.execute("ALTER SEQUENCE onboarding_turns_id_seq RENAME TO conversation_turns_id_seq")
    op.execute(
        "ALTER TABLE onboarding_turns RENAME CONSTRAINT onboarding_turns_session_id_fkey "
        "TO conversation_turns_session_id_fkey"
    )
    op.execute("ALTER TABLE onboarding_turns RENAME CONSTRAINT onboarding_turns_pkey TO conversation_turns_pkey")
    op.execute("ALTER INDEX ix_onboarding_turns_session_id RENAME TO ix_conversation_turns_session_id")
    op.rename_table("onboarding_turns", "conversation_turns")
