"""v2 시뮬레이션 API — service 는 가짜, 상태코드·응답·헤더·동기 실행만 본다."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.db import get_db
from app.features.persona.schemas import PersonaBrief, PersonaRef
from app.features.simulation.schemas import (
    AreaReport,
    DateSuggestion,
    MatchingReport,
    Overall,
    ReportConfidence,
    Turn,
)
from app.v2.simulation import api
from app.v2.simulation.schemas import V2SimulationResponse
from app.v2.simulation.service import JobInProgress, JobNotFound, PersonaNotFound, SimulationNotFound

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


def _job(**kw):
    base = dict(id="j1", status="pending", stage=None, simulation_id=None, error=None, created_at=NOW, finished_at=None)
    return SimpleNamespace(**{**base, **kw})


def _result(simulation_id: str = "s1") -> V2SimulationResponse:
    report = MatchingReport(
        simulation_id=simulation_id,
        persona_a_id="pa",
        persona_b_id="pb",
        overall=Overall(score=80, grade="GOOD", grade_label="좋음", headline="헤드라인", summary="요약"),
        areas=[
            AreaReport(
                area="intimacy",
                label="거리감",
                score=80,
                grade="GOOD",
                grade_label="좋음",
                comment="설명",
                dimensions=[],
            )
        ],
        date_suggestion=DateSuggestion(suggested=["카페"], avoid=["등산"], comment="추천"),
        confidence=ReportConfidence(accuracy=85, low_dimensions=[], note=""),
    )
    return V2SimulationResponse(
        simulation_id=simulation_id,
        me=PersonaBrief(persona_id="pa", nickname="민수"),
        partner=PersonaBrief(persona_id="pb", nickname="지은"),
        turns=1,
        transcript=[Turn(index=0, speaker="a", text="안녕하세요"), Turn(index=1, speaker="b", text="반가워요")],
        report=report,
        created_at=NOW,
        mode="persona_only",
    )


def _client(error=None, job=None, run_result=None):
    """run_result: runner 가 끝난 뒤 get_job 이 돌려줄 작업. 기본은 성공(s1)."""
    calls = []
    state = {"ran": False}

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
            if state["ran"]:
                return run_result or _job(status="succeeded", simulation_id="s1", finished_at=NOW)
            return job or _job()

        async def get(self, simulation_id):
            calls.append(("get", simulation_id))
            if error or simulation_id != "s1":
                raise error or SimulationNotFound(simulation_id)
            return _result(simulation_id)

        async def list_for(self, user_id):
            calls.append(("list", user_id))
            return []

    class FakeDB:
        async def commit(self):
            calls.append(("commit",))

        async def rollback(self):
            calls.append(("rollback",))

        def expire_all(self):
            calls.append(("expire_all",))

    async def fake_db():
        yield FakeDB()

    async def runner(job_id):
        calls.append(("run", job_id))
        state["ran"] = True

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_service] = lambda: FakeService()
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[api.get_runner] = lambda: runner
    return TestClient(app), calls


BODY = {"me_user_id": "u-a", "partner_user_id": "u-b"}


def test_post_waits_and_returns_201_with_transcript_and_report():
    client, calls = _client()
    r = client.post("/v2/simulation", json=BODY)
    assert r.status_code == 201
    body = r.json()
    assert body["simulation_id"] == "s1"
    assert [t["text"] for t in body["transcript"]] == ["안녕하세요", "반가워요"]
    assert body["report"]["overall"]["headline"] == "헤드라인"
    # 작업을 만들고 커밋 → 실행 → 캐시 비우고 다시 읽기 → 결과 조회 순서
    names = [c[0] for c in calls]
    assert names.index("run") < names.index("expire_all") < names.index("get_job") < names.index("get")
    assert names.index("commit") < names.index("run")


def test_post_503_when_run_fails():
    failed = _job(status="failed", error="speaker_mixup_unresolved", finished_at=NOW)
    client, calls = _client(run_result=failed)
    r = client.post("/v2/simulation", json=BODY)
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["reason"] == "speaker_mixup_unresolved"
    assert detail["job_id"] == "j1" and detail["simulation_id"] is None
    assert detail["message"] == "simulation failed: speaker_mixup_unresolved"
    assert not [c for c in calls if c[0] == "get"]


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


def test_no_internal_key_required(monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_api_key", "secret")
    client, _ = _client()
    assert client.post("/v2/simulation", json=BODY).status_code == 201
    assert client.get("/v2/simulation/jobs/j1").status_code == 200


def test_router_registered_in_main_app():
    from app.main import app

    paths = set(app.openapi()["paths"])
    assert "/ai/api/v2/simulation" in paths
    assert "/ai/api/v2/simulation/jobs/{job_id}" in paths
