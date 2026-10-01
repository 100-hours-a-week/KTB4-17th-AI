from datetime import UTC, datetime

import pytest
from conftest import seed_persona, with_db
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.db import get_db
from app.features.persona import api
from app.features.persona.models import OnboardingSession
from app.features.persona.repository import PersonaRepository
from app.features.persona.schemas import UpdateNicknameRequest, UpdateNicknameResponse
from app.features.persona.service import OnboardingService, UserNotFound

NOW = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)


def _client(service_override=None):
    calls = []

    class FakeService:
        async def update_nickname(self, user_id: str, nickname: str):
            calls.append((user_id, nickname))
            if user_id == "not-found":
                raise UserNotFound(user_id)
            return UpdateNicknameResponse(
                user_id=user_id,
                nickname=nickname,
                updated_sessions=1,
                updated_at=NOW,
            )

    class FakeDb:
        async def commit(self):
            pass

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_service] = lambda: service_override or FakeService()
    app.dependency_overrides[get_db] = lambda: FakeDb()
    return TestClient(app), calls


def test_update_nickname_request_validation():
    req = UpdateNicknameRequest(user_id="u1", nickname="민수")
    assert req.user_id == "u1"
    assert req.nickname == "민수"

    with pytest.raises(ValidationError):
        UpdateNicknameRequest(user_id="u1", nickname="")

    with pytest.raises(ValidationError):
        UpdateNicknameRequest(user_id="u1", nickname="   ")

    with pytest.raises(ValidationError):
        UpdateNicknameRequest(user_id="", nickname="민수")

    with pytest.raises(ValidationError):
        UpdateNicknameRequest(user_id="   ", nickname="민수")

    with pytest.raises(ValidationError):
        UpdateNicknameRequest(user_id="u1", nickname="a" * 21)

    with pytest.raises(ValidationError):
        UpdateNicknameRequest(user_id="u1", nickname="민수", extra="not_allowed")


def test_post_nickname_api_success():
    client, calls = _client()
    res = client.post("/v1/persona/nickname", json={"user_id": "u1", "nickname": "새닉네임"})
    assert res.status_code == 200
    data = res.json()
    assert data["user_id"] == "u1"
    assert data["nickname"] == "새닉네임"
    assert data["updated_sessions"] == 1
    assert calls == [("u1", "새닉네임")]


def test_post_nickname_api_not_found():
    client, calls = _client()
    res = client.post("/v1/persona/nickname", json={"user_id": "not-found", "nickname": "새닉네임"})
    assert res.status_code == 404
    assert "not-found" in res.json()["detail"]


def test_post_nickname_api_validation_error():
    client, _ = _client()
    res = client.post("/v1/persona/nickname", json={"user_id": "u1", "nickname": ""})
    assert res.status_code == 422


def test_post_nickname_db_integration():
    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="p1", user_id="u-real", nickname="원래이름")

        async with factory() as db:
            repo = PersonaRepository(db)
            svc = OnboardingService(repo)
            res = await svc.update_nickname("u-real", "바뀐이름")
            assert res.user_id == "u-real"
            assert res.nickname == "바뀐이름"
            assert res.updated_sessions == 1
            await db.commit()

        async with factory() as db:
            session = await db.get(OnboardingSession, "s-p1")
            assert session is not None
            assert session.nickname == "바뀐이름"

        async with factory() as db:
            repo = PersonaRepository(db)
            svc = OnboardingService(repo)
            with pytest.raises(UserNotFound):
                await svc.update_nickname("u-nonexistent", "아무개")

    import asyncio

    asyncio.run(with_db(scenario))
