from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.features.persona import api as persona_api
from app.features.persona.schemas import PersonaResponse
from app.main import app

EXPECTED_DOCUMENTED_ENDPOINTS = {
    ("GET", "/health"),
    ("POST", "/ai/api/v1/persona/nickname"),
    ("POST", "/ai/api/v1/persona/onboarding/start"),
    ("POST", "/ai/api/v1/persona/onboarding/{session_id}/answer"),
    ("POST", "/ai/api/v1/persona/onboarding/{session_id}/skip"),
    ("POST", "/ai/api/v1/persona/onboarding/{session_id}/finish"),
    ("POST", "/ai/api/v1/persona/{session_id}/build"),
    ("POST", "/ai/api/v1/persona/{persona_id}/confirm"),
    ("GET", "/ai/api/v1/persona/{session_id}"),
    ("POST", "/ai/api/v1/persona/{session_id}/supplement"),
    ("POST", "/ai/api/v1/practice/start"),
    ("POST", "/ai/api/v1/practice/{session_id}/opening"),
    ("POST", "/ai/api/v1/practice/{session_id}/messages"),
    ("POST", "/ai/api/v1/practice/{session_id}/retry"),
    ("POST", "/ai/api/v1/practice/{session_id}/opening/stream"),
    ("POST", "/ai/api/v1/practice/{session_id}/messages/stream"),
    ("POST", "/ai/api/v1/practice/{session_id}/retry/stream"),
    ("GET", "/ai/api/v1/practice"),
    ("GET", "/ai/api/v1/practice/{session_id}"),
    ("POST", "/ai/api/v1/practice/{session_id}/end"),
    ("POST", "/ai/api/v1/simulation"),
    ("GET", "/ai/api/v1/simulation"),
    ("POST", "/ai/api/v1/simulation/report/preview"),
    ("GET", "/ai/api/v1/simulation/report/preview"),
    ("GET", "/ai/api/v1/simulation/report/preview/{preview_id}"),
    ("GET", "/ai/api/v1/simulation/{simulation_id}"),
    ("GET", "/ai/api/v1/simulation/{simulation_id}/report"),
}


def test_openapi_contains_every_documented_endpoint():
    actual = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if method in {"get", "post", "put", "patch", "delete"}
    }

    assert actual == EXPECTED_DOCUMENTED_ENDPOINTS


def test_health_is_reachable_on_public_and_proxy_paths():
    client = TestClient(app)

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ai/api/health").json() == {"status": "ok"}


def _persona_read_client():
    calls = []
    session = SimpleNamespace(id="session-1")

    class FakeRepository:
        async def get_session(self, session_id):
            calls.append(("get_session", session_id))
            return session if session_id == session.id else None

    class FakeService:
        repo = FakeRepository()

        async def get_latest(self, loaded_session):
            calls.append(("get_latest", loaded_session.id))
            return PersonaResponse(persona_id="persona-1", scores={})

        async def supplement(self, loaded_session, dimension, answer):
            calls.append(("supplement", loaded_session.id, dimension, answer))
            return PersonaResponse(
                persona_id="persona-2",
                version=2,
                scores={},
                generated_at=datetime(2026, 9, 28, tzinfo=UTC),
            )

    class FakeDb:
        async def commit(self):
            calls.append(("commit",))

    test_app = FastAPI()
    test_app.include_router(persona_api.router)
    test_app.dependency_overrides[persona_api.get_service] = lambda: FakeService()
    test_app.dependency_overrides[get_db] = lambda: FakeDb()
    return TestClient(test_app), calls


def test_get_persona_endpoint_returns_latest_persona():
    client, calls = _persona_read_client()

    res = client.get("/v1/persona/session-1")

    assert res.status_code == 200
    assert res.json()["persona_id"] == "persona-1"
    assert calls == [("get_session", "session-1"), ("get_latest", "session-1")]


def test_supplement_endpoint_rebuilds_and_commits():
    client, calls = _persona_read_client()

    res = client.post(
        "/v1/persona/session-1/supplement",
        json={"dimension": "avoidance", "answer": "혼자만의 시간이 필요해요"},
    )

    assert res.status_code == 200
    assert res.json()["version"] == 2
    assert calls == [
        ("get_session", "session-1"),
        ("supplement", "session-1", "avoidance", "혼자만의 시간이 필요해요"),
        ("commit",),
    ]
