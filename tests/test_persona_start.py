import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.db import get_db
from app.features.persona import api
from app.features.persona.schemas import StartRequest
from app.features.persona.service import ONBOARDING_TOTAL_TURNS, OnboardingService


def test_start_request_has_no_total_turns():
    req = StartRequest(nickname="민수", user_id="u1")
    assert "total_turns" not in StartRequest.model_json_schema()["properties"]
    assert not hasattr(req, "total_turns")


def test_start_request_rejects_total_turns():
    with pytest.raises(ValidationError):
        StartRequest(nickname="민수", user_id="u1", total_turns=10)


def test_service_start_creates_session_with_constant():
    assert ONBOARDING_TOTAL_TURNS == 10
    captured = {}

    async def create_session(nickname, total_turns, user_id):
        captured["args"] = (nickname, total_turns, user_id)
        return "session"

    async def ask_next(session):
        return session

    svc = OnboardingService.__new__(OnboardingService)
    svc.repo = SimpleNamespace(create_session=create_session)
    svc._ask_next = ask_next

    assert asyncio.run(svc.start("민수", "u1")) == "session"
    assert captured["args"] == ("민수", 10, "u1")


def _client():
    calls = []

    class FakeService:
        async def start(self, nickname, user_id):
            calls.append((nickname, user_id))
            return {"session_id": "s", "utterance": "안녕", "progress": "1/10"}

    class FakeDb:
        async def commit(self):
            pass

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_service] = lambda: FakeService()
    app.dependency_overrides[get_db] = lambda: FakeDb()
    return TestClient(app), calls


def test_api_start_without_total_turns_ok():
    client, calls = _client()
    res = client.post("/v1/persona/onboarding/start", json={"nickname": "민수", "user_id": "u1"})
    assert res.status_code == 200
    assert calls == [("민수", "u1")]


def test_api_start_with_total_turns_422():
    client, calls = _client()
    res = client.post("/v1/persona/onboarding/start", json={"nickname": "민수", "user_id": "u1", "total_turns": 10})
    assert res.status_code == 422
    assert calls == []
