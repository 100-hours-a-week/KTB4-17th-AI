from __future__ import annotations

import asyncio
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.db import Base
from app.features.feedback_cards.models import FeedbackCard, FeedbackCardAnswer
from scripts.load_feedback_cards import SeedCard, load_cards, read_cards

MIGRATION = Path(__file__).resolve().parents[1] / "alembic/versions/2026_11_12_1000-fbc20261112a_feedback_cards.py"


def seed_row(**values):
    return {
        "card_id": "fc_seed",
        "user_id": "1001",
        "context_question": None,
        "utterance": "저는 소금빵을 좋아해요",
        "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        **values,
    }


def test_migration_upgrade_constraints_and_downgrade(tmp_path):
    spec = importlib.util.spec_from_file_location("feedback_migration", MIGRATION)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert set(inspect(connection).get_table_names()) == {"feedback_card_candidates", "feedback_card_answers"}
            # 적재된 모델과 마이그레이션이 같은 테이블을 읽고 쓸 수 있다.
            connection.execute(
                FeedbackCard.__table__.insert(),
                {
                    "id": "fc_1",
                    "user_id": "1001",
                    "context_question": None,
                    "utterance": "빵 좋아해요",
                    "expires_at": datetime.now(UTC) + timedelta(days=1),
                    "status": "SHOWN",
                },
            )
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(
                        FeedbackCard.__table__.insert(),
                        {
                            "id": "fc_2",
                            "user_id": "1001",
                            "utterance": "떡 좋아해요",
                            "expires_at": datetime.now(UTC) + timedelta(days=1),
                            "status": "SHOWN",
                        },
                    )
            migration.downgrade()
            assert inspect(connection).get_table_names() == []
    engine.dispose()


def test_loader_is_idempotent_and_rolls_back_changed_ids(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'loader.db'}")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(
                Base.metadata.create_all, tables=[FeedbackCard.__table__, FeedbackCardAnswer.__table__]
            )
        original = SeedCard.model_validate(seed_row())
        async with factory.begin() as db:
            assert await load_cards(db, [original]) == 1
        async with factory.begin() as db:
            assert await load_cards(db, [original]) == 0
        with pytest.raises(ValueError, match="내용이 다릅니다"):
            async with factory.begin() as db:
                await load_cards(
                    db,
                    [
                        SeedCard.model_validate(seed_row(card_id="fc_new")),
                        SeedCard.model_validate(seed_row(utterance="다른 문장")),
                    ],
                )
        async with factory() as db:
            cards = list((await db.scalars(select(FeedbackCard))).all())
            assert len(cards) == 1
            assert cards[0].utterance == original.utterance
        await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change",
    [
        {"utterance": ""},
        {"utterance": "가" * 301},
        {"context_question": "가" * 201},
        {"expires_at": "2026-11-12T12:00:00"},
        {"extra": True},
    ],
)
def test_loader_rejects_invalid_rows_before_writing(tmp_path, change):
    path = tmp_path / "cards.jsonl"
    path.write_text(json.dumps(seed_row(**change)), encoding="utf-8")
    with pytest.raises(ValueError):
        read_cards(path)


def test_loader_rejects_duplicate_ids_and_expired_cards(tmp_path):
    path = tmp_path / "cards.jsonl"
    row = json.dumps(seed_row())
    path.write_text(f"{row}\n{row}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="중복 card_id"):
        read_cards(path)
    path.write_text(json.dumps(seed_row(expires_at="2020-01-01T00:00:00Z")), encoding="utf-8")
    with pytest.raises(ValueError, match="미래 시각"):
        read_cards(path)
