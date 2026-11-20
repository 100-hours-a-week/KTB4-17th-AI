"""mlops adapter tables — 사용자별 말투 어댑터 학습·서빙 테이블과 연습 세션의 어댑터 이름

persona_raw_conversation : 정제 전 원천 대화. 대화 하나가 한 행 (user_id, conversation_id 유니크)
persona_training_sample  : 본인 발화 하나가 한 행. 판정 결과와 마스킹된 문맥·정답
persona_adapter          : 사용자별 어댑터 버전. 사용자당 active 는 하나 (부분 유니크 인덱스)
practice_sessions.adapter_model : 세션이 쓴 어댑터 서빙 이름 ("{user_id}-v{n}")

열 정의는 persona-mlops src/training/infra/sql_models.py 와 같다 (app/features/mlops/models.py).
배치(persona-mlops)가 세 테이블을 읽고 쓰고, API 서버는 persona_adapter 만 읽는다. 기존 테이블은 열 하나만 더한다.

Revision ID: a7d8e9f0a1b2
Revises: z6c7d8e9f0a1
Create Date: 2026-11-20 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7d8e9f0a1b2"
down_revision: str | Sequence[str] | None = "z6c7d8e9f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
_SOURCE_CHECK = "source IN ('practice', 'upload', 'chat')"


def upgrade() -> None:
    op.create_table(
        "persona_raw_conversation",
        sa.Column("id", _ID, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("turn_count", sa.Integer(), nullable=False),
        sa.Column("self_turn_count", sa.Integer(), nullable=False),
        sa.Column("turns", _JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "conversation_id", name="uq_persona_raw_conversation_user_conversation"),
        sa.CheckConstraint(_SOURCE_CHECK, name="ck_persona_raw_conversation_source"),
    )
    op.create_index(
        "ix_persona_raw_conversation_user_id_started_at",
        "persona_raw_conversation",
        ["user_id", "started_at"],
    )

    op.create_table(
        "persona_training_sample",
        sa.Column("id", _ID, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("context", _JSON, nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("drop_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # 사람당 최근 유효 발화 창 안인지. 창 밖으로 밀린 행도 지우지 않는다
        sa.Column("in_window", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.CheckConstraint(_SOURCE_CHECK, name="ck_persona_training_sample_source"),
        sa.CheckConstraint("verdict IN ('pass', 'masked', 'dropped')", name="ck_persona_training_sample_verdict"),
    )
    op.create_index("ix_persona_training_sample_user_id", "persona_training_sample", ["user_id"])
    op.create_index("ix_persona_training_sample_conversation_id", "persona_training_sample", ["conversation_id"])
    op.create_index("ix_persona_training_sample_user_id_verdict", "persona_training_sample", ["user_id", "verdict"])

    op.create_table(
        "persona_adapter",
        sa.Column("user_id", sa.String(length=64), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("base_model_id", sa.String(length=255), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("metrics", _JSON, nullable=False),
        sa.Column("artifact_uri", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('training', 'ready', 'active', 'failed', 'deleted')", name="ck_persona_adapter_status"
        ),
    )
    # 사용자당 active 어댑터는 하나뿐이다
    op.create_index(
        "uq_persona_adapter_active_user",
        "persona_adapter",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )

    # NULL 허용 — 기존 세션과 어댑터 없는 세션은 NULL 로 남는다
    op.add_column("practice_sessions", sa.Column("adapter_model", sa.String(length=96), nullable=True))


def downgrade() -> None:
    op.drop_column("practice_sessions", "adapter_model")
    op.drop_index("uq_persona_adapter_active_user", table_name="persona_adapter")
    op.drop_table("persona_adapter")
    op.drop_index("ix_persona_training_sample_user_id_verdict", table_name="persona_training_sample")
    op.drop_index("ix_persona_training_sample_conversation_id", table_name="persona_training_sample")
    op.drop_index("ix_persona_training_sample_user_id", table_name="persona_training_sample")
    op.drop_table("persona_training_sample")
    op.drop_index("ix_persona_raw_conversation_user_id_started_at", table_name="persona_raw_conversation")
    op.drop_table("persona_raw_conversation")
