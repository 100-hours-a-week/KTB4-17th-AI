"""MLOps 테이블 마이그레이션 — 모델과 같은 스키마, 제약, 부분 유니크 인덱스, 되돌리기, PostgreSQL DDL."""

import importlib.util
import io
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from app.core.db import Base
from app.features.mlops.models import PersonaAdapter, PersonaRawConversation, PersonaTrainingSample

MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic/versions/2026_11_20_1000-a7d8e9f0a1b2_mlops_adapter_tables.py"
)
TABLES = {"persona_raw_conversation", "persona_training_sample", "persona_adapter"}
NOW = datetime(2026, 11, 20, 1, 0, tzinfo=UTC)


def _migration():
    spec = importlib.util.spec_from_file_location("mlops_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _practice_sessions_only(connection) -> None:
    """upgrade 가 열을 더하는 기존 테이블만 미리 만든다 (앞선 마이그레이션의 결과 대신)."""
    meta = sa.MetaData()
    sa.Table("practice_sessions", meta, sa.Column("id", sa.String(32), primary_key=True))
    meta.create_all(connection)


def _adapter(**values):
    return {
        "user_id": "owner-1",
        "version": 1,
        "status": "ready",
        "base_model_id": "org/persona-base@abc",
        "sample_count": 300,
        "metrics": {"gate": {"passed": True}},
        "artifact_uri": "hf://org/persona-adapters/owner-1/v1",
        "created_at": NOW,
        "activated_at": None,
        **values,
    }


def test_revision_is_new_head_on_top_of_dev_head():
    migration = _migration()
    assert migration.revision == "a7d8e9f0a1b2"
    assert migration.down_revision == "z6c7d8e9f0a1"


def test_upgrade_matches_models_and_downgrade_removes_everything(tmp_path):
    migration = _migration()
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection:
        _practice_sessions_only(connection)
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()

        tables = set(inspect(connection).get_table_names())
        assert TABLES <= tables
        assert "adapter_model" in {c["name"] for c in inspect(connection).get_columns("practice_sessions")}

        # 모델과 마이그레이션이 같은 스키마인지 autogenerate 로 비교한다 (새 테이블만. 열·타입·인덱스·유니크)
        context = MigrationContext.configure(
            connection,
            opts={"compare_type": True, "include_name": lambda name, kind, _: kind != "table" or name in TABLES},
        )
        meta = sa.MetaData()
        for model in (PersonaRawConversation, PersonaTrainingSample, PersonaAdapter):
            model.__table__.to_metadata(meta)
        assert compare_metadata(context, meta) == []

        indexes = {i["name"]: i for i in inspect(connection).get_indexes("persona_training_sample")}
        assert {
            "ix_persona_training_sample_user_id",
            "ix_persona_training_sample_conversation_id",
            "ix_persona_training_sample_user_id_verdict",
        } <= set(indexes)

        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert not TABLES & set(inspect(connection).get_table_names())
        assert "adapter_model" not in {c["name"] for c in inspect(connection).get_columns("practice_sessions")}


def test_constraints_and_one_active_adapter_per_user(tmp_path):
    migration = _migration()
    engine = create_engine(f"sqlite:///{tmp_path / 'constraints.db'}")
    with engine.begin() as connection:
        _practice_sessions_only(connection)
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()

    with engine.begin() as connection:
        connection.execute(PersonaAdapter.__table__.insert(), [_adapter(), _adapter(version=2, status="active")])
        # 다른 사용자의 active 는 따로 센다
        connection.execute(PersonaAdapter.__table__.insert(), _adapter(user_id="owner-2", status="active"))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(PersonaAdapter.__table__.insert(), _adapter(version=3, status="active"))
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(PersonaAdapter.__table__.insert(), _adapter(version=4, status="live"))

    raw = {
        "user_id": "owner-1",
        "conversation_id": "c1",
        "started_at": NOW,
        "ended_at": NOW,
        "turn_count": 2,
        "self_turn_count": 1,
        "turns": [{"speaker": "self", "text": "안녕하세요"}, {"speaker": "other", "text": "반가워요"}],
        "created_at": NOW,
    }
    with engine.begin() as connection:
        # 카톡 업로드(upload)는 mlops 에 없던 값이라 여기서 더했다
        connection.execute(PersonaRawConversation.__table__.insert(), {**raw, "source": "upload"})
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(PersonaRawConversation.__table__.insert(), {**raw, "source": "practice"})  # 같은 키
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(PersonaRawConversation.__table__.insert(), {**raw, "conversation_id": "c2", "source": "x"})

    sample = {
        "user_id": "owner-1",
        "source": "practice",
        "conversation_id": "c1",
        "context": [{"speaker": "other", "text": "반가워요"}],
        "target": "저도 반가워요",
        "verdict": "pass",
        "drop_reason": None,
        "created_at": NOW,
    }
    with engine.begin() as connection:
        connection.execute(sa.insert(PersonaTrainingSample.__table__).values(**sample))
        # in_window 는 기본값 TRUE (서버 기본값). ORM 기본값을 거치지 않게 열을 빼고 넣는다
        connection.execute(
            sa.text(
                "INSERT INTO persona_training_sample (user_id, source, conversation_id, context, target, verdict, "
                "created_at) VALUES ('owner-1', 'chat', 'c3', '[]', '네', 'masked', '2026-11-20 01:00:00')"
            )
        )
        rows = connection.execute(sa.text("SELECT id, in_window FROM persona_training_sample ORDER BY id")).all()
        assert [r.in_window for r in rows] == [1, 1]
        assert rows[0].id < rows[1].id
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(sa.insert(PersonaTrainingSample.__table__).values(**{**sample, "verdict": "maybe"}))


def test_postgresql_ddl_uses_jsonb_and_partial_unique_index():
    """실제 DB 없이 PostgreSQL 용 SQL 만 만든다 (alembic upgrade --sql 과 같다)."""
    migration = _migration()
    buf = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buf})
    with Operations.context(context):
        migration.upgrade()
    sql = buf.getvalue()
    assert "turns JSONB NOT NULL" in sql
    assert "metrics JSONB NOT NULL" in sql
    assert "id BIGSERIAL NOT NULL" in sql
    assert (
        "CREATE UNIQUE INDEX uq_persona_adapter_active_user ON persona_adapter (user_id) WHERE status = 'active'" in sql
    )
    assert "CHECK (source IN ('practice', 'upload', 'chat'))" in sql
    assert "in_window BOOLEAN DEFAULT true NOT NULL" in sql
    assert "ALTER TABLE practice_sessions ADD COLUMN adapter_model VARCHAR(96)" in sql


def test_models_are_registered_on_shared_metadata():
    assert TABLES <= set(Base.metadata.tables)
    assert "adapter_model" in Base.metadata.tables["practice_sessions"].c
