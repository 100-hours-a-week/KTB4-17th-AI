"""HTTP 계약을 실제 service/repository와 파일 SQLite로 확인한다."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import Base, get_db
from app.features.feedback_cards import api
from app.features.feedback_cards.models import QUESTION, FeedbackCard, FeedbackCardAnswer
from app.features.feedback_cards.service import FeedbackCardService

T0 = datetime(2026, 11, 12, 3, tzinfo=UTC)
PREFIX = "/ai/api/v1/feedback-cards"
HEADERS = {"X-Internal-Api-Key": "feedback-test-key"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_api_key", HEADERS["X-Internal-Api-Key"])
    database_url = os.environ.get("FEEDBACK_TEST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'feedback.db'}")
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    clock = [T0]

    async def real_db():
        async with factory() as db:
            yield db

    def service(db=Depends(get_db)):
        return FeedbackCardService(db, now=lambda: clock[0])

    async def create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[FeedbackCard.__table__, FeedbackCardAnswer.__table__])

    async def cleanup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all, tables=[FeedbackCard.__table__, FeedbackCardAnswer.__table__])
        await engine.dispose()

    app = FastAPI()
    app.include_router(api.router, prefix="/ai/api")
    app.dependency_overrides[get_db] = real_db
    app.dependency_overrides[api.get_service] = service

    async def seed(**values):
        async with factory.begin() as db:
            card = FeedbackCard(
                **{
                    "id": "fc_1",
                    "user_id": "1001",
                    "context_question": "빵 좋아하세요?",
                    "utterance": "저는 소금빵을 좋아해요",
                    "created_at": T0,
                    "expires_at": T0 + timedelta(days=7),
                    **values,
                }
            )
            db.add(card)

    async def stored():
        async with factory() as db:
            cards = list((await db.scalars(select(FeedbackCard).order_by(FeedbackCard.id))).all())
            answers = list((await db.scalars(select(FeedbackCardAnswer))).all())
            return cards, answers

    with TestClient(app, headers=HEADERS) as client:
        client.portal.call(create_tables)
        yield SimpleNamespace(
            app=app,
            client=client,
            clock=clock,
            factory=factory,
            seed=lambda **values: client.portal.call(lambda: seed(**values)),
            stored=lambda: client.portal.call(stored),
        )
        client.portal.call(cleanup)


def next_card(env, user_id="1001"):
    return env.client.post(f"{PREFIX}/next", json={"user_id": user_id})


def answer(env, card_id="fc_1", **values):
    body = {
        "user_id": "1001",
        "answer": "NO",
        "correction_text": "떡을 더 좋아해요",
        "answered_at": T0.isoformat(),
        "consent_version": "terms-2026-11",
        **values,
    }
    return env.client.post(f"{PREFIX}/{card_id}/answer", json=body)


def test_next_replays_same_card_without_exposing_second_candidate(env):
    env.seed()
    env.seed(id="fc_2")
    first = next_card(env)
    assert first.status_code == 200
    assert first.json() == {
        "card_id": "fc_1",
        "context_question": "빵 좋아하세요?",
        "utterance": "저는 소금빵을 좋아해요",
        "question": QUESTION,
        "expires_at": (T0 + timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
    }
    env.clock[0] += timedelta(hours=1)
    assert next_card(env).json() == first.json()
    cards, _ = env.stored()
    assert [card.status for card in cards] == ["SHOWN", "READY"]
    assert cards[0].shown_at.replace(tzinfo=UTC) == T0


def test_no_card_and_other_users_card_return_empty_204(env):
    env.seed(user_id="other")
    response = next_card(env)
    assert response.status_code == 204
    assert response.content == b""


def test_null_context_and_candidate_deadline_are_preserved(env):
    env.seed(context_question=None, expires_at=T0 + timedelta(minutes=10))
    body = next_card(env).json()
    assert body["context_question"] is None
    assert datetime.fromisoformat(body["expires_at"]) == T0 + timedelta(minutes=10)


def test_expired_candidates_are_skipped_and_expired_shown_card_is_replaced(env):
    env.seed(expires_at=T0)
    env.seed(id="fc_2", expires_at=T0 + timedelta(hours=1))
    env.seed(id="fc_3")
    assert next_card(env).json()["card_id"] == "fc_2"
    env.clock[0] += timedelta(hours=1)
    assert next_card(env).json()["card_id"] == "fc_3"
    cards, _ = env.stored()
    assert [card.status for card in cards] == ["EXPIRED", "EXPIRED", "SHOWN"]


@pytest.mark.parametrize("choice,correction", [("YES", None), ("NO", None), ("NO", "떡을 더 좋아해요")])
def test_answer_persists_once_and_replays_after_expiry(env, choice, correction):
    env.seed()
    next_card(env)
    first = answer(env, answer=choice, correction_text=correction)
    assert first.status_code == 200
    assert first.json() == {"card_id": "fc_1", "status": "ANSWERED"}
    env.clock[0] += timedelta(days=2)
    assert answer(env, answer=choice, correction_text=correction).json() == first.json()
    cards, answers = env.stored()
    assert cards[0].status == "ANSWERED"
    assert len(answers) == 1
    assert answers[0].answer == choice
    assert answers[0].correction_text == correction
    assert answers[0].consent_version == "terms-2026-11"
    assert next_card(env).status_code == 204


@pytest.mark.parametrize(
    "change",
    [
        {"answer": "YES", "correction_text": None},
        {"correction_text": "다른 답변"},
        {"answered_at": (T0 + timedelta(seconds=1)).isoformat()},
        {"consent_version": "terms-2026-12"},
    ],
)
def test_changed_answer_returns_conflict_without_overwriting(env, change):
    env.seed()
    next_card(env)
    assert answer(env).status_code == 200
    changed = answer(env, **change)
    assert changed.status_code == 409
    assert changed.json() == {"detail": "card already answered"}
    assert env.stored()[1][0].correction_text == "떡을 더 좋아해요"


def test_omitted_null_and_equivalent_timezone_are_same_answer(env):
    env.seed()
    next_card(env)
    body = {
        "user_id": "1001",
        "answer": "YES",
        "answered_at": "2026-11-12T12:00:00+09:00",
        "consent_version": "terms-2026-11",
    }
    assert env.client.post(f"{PREFIX}/fc_1/answer", json=body).status_code == 200
    assert answer(env, answer="YES", correction_text=None).status_code == 200


def test_unknown_card_and_wrong_owner_are_404_and_403(env):
    assert answer(env).status_code == 404
    env.seed()
    next_card(env)
    assert answer(env, user_id="other").status_code == 403
    assert not env.stored()[1]


def test_expiry_uses_server_time_and_includes_exact_deadline(env):
    env.seed(expires_at=T0 + timedelta(minutes=1))
    next_card(env)
    env.clock[0] += timedelta(minutes=1)
    response = answer(env)  # answered_at은 만료 전이어도 서버에 만료 후 도착했다.
    assert response.status_code == 409
    assert response.json() == {"detail": "card expired"}
    assert not env.stored()[1]


def test_unshown_card_cannot_be_answered(env):
    env.seed()
    assert answer(env).status_code == 409
    assert not env.stored()[1]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"user_id": ""},
        {"user_id": " "},
        {"user_id": "x" * 65},
        {"user_id": 1},
        {"user_id": "1001", "request_id": "old"},
    ],
)
def test_next_rejects_invalid_input(env, payload):
    assert env.client.post(f"{PREFIX}/next", json=payload).status_code == 422


@pytest.mark.parametrize(
    "change",
    [
        {"answer": "MAYBE"},
        {"answer": "yes"},
        {"answer": "YES", "correction_text": "수정"},
        {"correction_text": ""},
        {"correction_text": " "},
        {"correction_text": "가" * 201},
        {"answered_at": "2026-11-12T12:00:00"},
        {"answered_at": "not-a-date"},
        {"answered_at": 1794452400},
        {"answered_at": "1794452400"},
        {"consent_version": ""},
        {"consent_version": "x" * 65},
        {"unexpected": True},
    ],
)
def test_answer_rejects_invalid_input(env, change):
    assert answer(env, **change).status_code == 422
    assert not env.stored()[1]


@pytest.mark.parametrize("missing", ["user_id", "answer", "answered_at", "consent_version"])
def test_answer_requires_fields(env, missing):
    body = {"user_id": "1001", "answer": "YES", "answered_at": T0.isoformat(), "consent_version": "v1"}
    del body[missing]
    assert env.client.post(f"{PREFIX}/fc_1/answer", json=body).status_code == 422


def test_card_id_length_is_validated(env):
    assert answer(env, card_id="x" * 65).status_code == 422


@pytest.mark.parametrize("path", ["next", "fc_1/answer"])
@pytest.mark.parametrize("key", [None, "incorrect"])
def test_internal_key_is_required_for_both_routes(env, path, key):
    with TestClient(env.app) as client:
        response = client.post(
            f"{PREFIX}/{path}", json={"user_id": "1001"}, headers={"X-Internal-Api-Key": key} if key else {}
        )
    assert response.status_code == 401


def test_unconfigured_key_does_not_disable_authentication(env, monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_api_key", "")
    assert next_card(env).status_code == 401


def test_concurrent_next_and_answers_keep_single_card_and_answer(env):
    env.seed()
    env.seed(id="fc_2")

    async def concurrent():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=env.app), base_url="http://test", headers=HEADERS
        ) as client:
            responses = await asyncio.gather(
                *(client.post(f"{PREFIX}/next", json={"user_id": "1001"}) for _ in range(8))
            )
            assert {r.status_code for r in responses} == {200}
            assert {r.json()["card_id"] for r in responses} == {"fc_1"}
            body = {"user_id": "1001", "answer": "YES", "answered_at": T0.isoformat(), "consent_version": "v1"}
            answers = await asyncio.gather(*(client.post(f"{PREFIX}/fc_1/answer", json=body) for _ in range(8)))
            assert {r.status_code for r in answers} == {200}
            conflicts = await asyncio.gather(
                *(client.post(f"{PREFIX}/fc_1/answer", json={**body, "answer": "NO"}) for _ in range(4))
            )
            assert {r.status_code for r in conflicts} == {409}
        async with env.factory() as db:
            assert await db.scalar(select(func.count()).select_from(FeedbackCardAnswer)) == 1

    env.client.portal.call(concurrent)


def test_real_app_registers_both_paths():
    from app.main import app

    paths = app.openapi()["paths"]
    assert "post" in paths[f"{PREFIX}/next"]
    assert "post" in paths[f"{PREFIX}/{{card_id}}/answer"]
