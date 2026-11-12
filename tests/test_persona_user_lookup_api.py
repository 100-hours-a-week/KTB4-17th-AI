"""GET /v1/persona/users/{user_id} — 마이페이지가 그 유저의 확정 페르소나를 꺼내는 입구."""

import asyncio

import httpx
import pytest
from conftest import seed_persona, with_db
from fastapi import FastAPI

from app.core.config import get_settings
from app.core.db import get_db
from app.features.persona import api

KEY = "test-internal-key"


@pytest.fixture(autouse=True)
def _internal_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_api_key", KEY)


def _get(user_id, *, seeds=(), headers=None):
    """seeds 로 페르소나를 저장해 두고 user_id 로 조회한 응답."""

    async def scenario(factory):
        for seed in seeds:
            async with factory() as db:
                await seed_persona(db, **seed)

        async def db_dep():
            async with factory() as db:
                yield db

        app = FastAPI()
        app.include_router(api.router)
        app.dependency_overrides[get_db] = db_dep
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(
                f"/v1/persona/users/{user_id}",
                headers={"X-Internal-Api-Key": KEY} if headers is None else headers,
            )

    return asyncio.run(with_db(scenario))


def test_returns_confirmed_persona_of_that_user():
    res = _get(
        "1001",
        seeds=[
            dict(persona_id="p1", user_id="1001", nickname="민수", scores={"avoidance": 78}, mbti="INTP"),
            dict(persona_id="p2", user_id="2002", nickname="지은", scores={"avoidance": 20}),
        ],
    )

    assert res.status_code == 200
    body = res.json()
    assert body["persona_id"] == "p1"
    assert body["is_confirmed"] is True
    assert body["mbti"] == "INTP"
    assert body["scores"]["avoidance"] == 78


def test_draft_only_user_is_not_found():
    """/confirm 전 초안은 마이페이지에 보여 주지 않는다."""
    res = _get("1001", seeds=[dict(persona_id="p1", user_id="1001", nickname="민수", confirmed=False)])

    assert res.status_code == 404
    assert res.json() == {"detail": "persona not found"}


def test_unknown_user_is_not_found():
    res = _get("9999")

    assert res.status_code == 404


def test_missing_internal_key_is_rejected():
    res = _get("1001", seeds=[dict(persona_id="p1", user_id="1001", nickname="민수")], headers={})

    assert res.status_code == 401


def test_too_long_user_id_is_rejected():
    res = _get("x" * 65)

    assert res.status_code == 422
