"""v2 시뮬레이션 API — service 는 가짜, 상태코드·응답·헤더·백그라운드 예약·인증만 본다."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.db import get_db
from app.features.persona.schemas import PersonaRef
from app.v2.simulation import api
from app.v2.simulation.service import JobInProgress, JobNotFound, PersonaNotFound, SimulationNotFound

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def _job(**kw):
    base = dict(id="j1", status="pending", stage=None, simulation_id=None, error=None, created_at=NOW, finished_at=None)
    return SimpleNamespace(**{**base, **kw})


def _client(error=None, job=None):
    calls = []

    class FakeService:
        async def start(self, req):
            calls.append(("start", req.me_user_id))
            if error:
                raise error
            return _job()

        async def get_job(self, job_id):
            calls.append(("get_job", job_id))
            if error:
                raise error
            return job or _job()

        async def get(self, simulation_id):
            raise error or SimulationNotFound(simulation_id)

        async def list_for(self, user_id):
            calls.append(("list", user_id))
            return []

    class FakeDB:
        async def commit(self):
            calls.append(("commit",))

        async def rollback(self):
            calls.append(("rollback",))

    async def fake_db():
        yield FakeDB()

    async def runner(job_id):
        calls.append(("run", job_id))

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_service] = lambda: FakeService()
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[api.get_runner] = lambda: runner
    return TestClient(app), calls


BODY = {"me_user_id": "u-a", "partner_user_id": "u-b"}


def test_post_returns_202_and_schedules_job():
    client, calls = _client()
    r = client.post("/v2/simulation", json=BODY)
    assert r.status_code == 202
    assert r.json() == {"job_id": "j1", "status": "pending"}
    assert ("commit",) in calls and ("run", "j1") in calls


def test_post_404_when_persona_missing():
    client, calls = _client(error=PersonaNotFound("partner", PersonaRef(user_id="u-b")))
    r = client.post("/v2/simulation", json=BODY)
    assert r.status_code == 404
    assert not [c for c in calls if c[0] == "run"]


def test_post_409_with_existing_job_id():
    client, calls = _client(error=JobInProgress("j0"))
    r = client.post("/v2/simulation", json=BODY)
    assert r.status_code == 409
    assert r.json()["detail"]["job_id"] == "j0"
    assert not [c for c in calls if c[0] == "run"]


def test_post_validation():
    client, _ = _client()
    assert client.post("/v2/simulation", json={"me_user_id": "u-a"}).status_code == 422
    assert client.post("/v2/simulation", json={**BODY, "turns": 16}).status_code == 422


def test_get_job_running_has_retry_after_and_stage():
    client, _ = _client(job=_job(status="running", stage="verifying"))
    r = client.get("/v2/simulation/jobs/j1")
    assert r.status_code == 200
    assert r.headers["Retry-After"] == "2"
    body = r.json()
    assert body["status"] == "running" and body["stage"] == "verifying" and body["reason"] is None


def test_get_job_failed_shows_reason():
    client, _ = _client(job=_job(status="failed", error="speaker_mixup_unresolved", finished_at=NOW))
    r = client.get("/v2/simulation/jobs/j1")
    assert r.json()["reason"] == "speaker_mixup_unresolved"
    assert "Retry-After" not in r.headers


def test_get_job_404():
    client, _ = _client(error=JobNotFound("x"))
    assert client.get("/v2/simulation/jobs/x").status_code == 404


def test_get_simulation_404_and_list():
    client, calls = _client()
    assert client.get("/v2/simulation/nope").status_code == 404
    r = client.get("/v2/simulation", params={"user_id": "u-a"})
    assert r.status_code == 200 and r.json() == []
    assert ("list", "u-a") in calls
    assert client.get("/v2/simulation").status_code == 422


def test_internal_key_required_when_configured(monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_api_key", "secret")
    client, _ = _client()
    assert client.post("/v2/simulation", json=BODY).status_code == 401
    r = client.post("/v2/simulation", json=BODY, headers={"X-Internal-Api-Key": "secret"})
    assert r.status_code == 202


def test_router_registered_in_main_app():
    from app.main import app

    paths = set(app.openapi()["paths"])
    assert "/ai/api/v2/simulation" in paths
    assert "/ai/api/v2/simulation/jobs/{job_id}" in paths
