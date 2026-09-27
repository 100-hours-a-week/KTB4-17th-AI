"""move MBTI from user_profiles to personas

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-27 22:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """최신 확정 페르소나에 기존 MBTI를 옮긴 뒤 사용자 프로필 테이블을 제거한다."""
    op.add_column("personas", sa.Column("mbti", sa.String(length=4), nullable=True))
    op.execute(
        """
        WITH latest_confirmed AS (
            SELECT DISTINCT ON (user_id) id, user_id
            FROM personas
            WHERE is_confirmed = TRUE
              AND user_id <> 'unknown'
            ORDER BY user_id, confirmed_at DESC NULLS LAST, created_at DESC, version DESC, id DESC
        )
        UPDATE personas AS persona
        SET mbti = UPPER(BTRIM(profile.mbti))
        FROM latest_confirmed AS latest
        JOIN user_profiles AS profile ON profile.user_id = latest.user_id
        WHERE persona.id = latest.id
          AND UPPER(BTRIM(profile.mbti)) IN (
              'ENFJ', 'ENFP', 'ENTJ', 'ENTP', 'ESFJ', 'ESFP', 'ESTJ', 'ESTP',
              'INFJ', 'INFP', 'INTJ', 'INTP', 'ISFJ', 'ISFP', 'ISTJ', 'ISTP'
          )
        """
    )
    op.create_check_constraint(
        "ck_personas_mbti_confirmed_only",
        "personas",
        "mbti IS NULL OR is_confirmed",
    )
    op.create_check_constraint(
        "ck_personas_mbti_valid",
        "personas",
        "mbti IS NULL OR mbti IN ("
        "'ENFJ', 'ENFP', 'ENTJ', 'ENTP', 'ESFJ', 'ESFP', 'ESTJ', 'ESTP', "
        "'INFJ', 'INFP', 'INTJ', 'INTP', 'ISFJ', 'ISFP', 'ISTJ', 'ISTP'"
        ")",
    )
    op.drop_table("user_profiles")


def downgrade() -> None:
    """사용자별 최신 확정 페르소나의 MBTI로 user_profiles를 복구한다."""
    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("mbti", sa.String(length=4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.execute(
        """
        INSERT INTO user_profiles (user_id, mbti, created_at, updated_at)
        SELECT DISTINCT ON (user_id)
            user_id,
            mbti,
            COALESCE(confirmed_at, created_at),
            COALESCE(confirmed_at, created_at)
        FROM personas
        WHERE is_confirmed = TRUE
          AND user_id <> 'unknown'
          AND mbti IS NOT NULL
        ORDER BY user_id, confirmed_at DESC NULLS LAST, created_at DESC, version DESC, id DESC
        """
    )
    op.drop_constraint("ck_personas_mbti_valid", "personas", type_="check")
    op.drop_constraint("ck_personas_mbti_confirmed_only", "personas", type_="check")
    op.drop_column("personas", "mbti")
