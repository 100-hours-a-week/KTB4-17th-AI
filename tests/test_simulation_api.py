import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.features.persona.schemas import PersonaBrief, PersonaRef, PersonaResponse
from app.features.simulation import api
from app.features.simulation.agents import SimulationFailed
from app.features.simulation.schemas import (
    MatchingReport,
    ReportPreviewDetail,
    ReportPreviewResponse,
    ReportPreviewSummary,
    SimulationResponse,
    Transcript,
    Turn,
)
from app.features.simulation.service import (
    PersonaNotFound,
    ReportPreviewNotFound,
    SimulationAlreadyRunning,
    SimulationNotFound,
)

REPORT = MatchingReport.model_validate(json.loads(Path("tests/fixtures/matching_report.json").read_text()))


def _response() -> SimulationResponse:
    return SimulationResponse(
        simulation_id="sim_demo",
        me=PersonaBrief(persona_id="pa_001", nickname="민수"),
        partner=PersonaBrief(persona_id="pb_002", nickname="지수"),
        turns=3,
        transcript=[Turn(index=0, speaker="a", text="안녕하세요"), Turn(index=1, speaker="b", text="반가워요")],
        report=REPORT,
        created_at=REPORT.generated_at,
    )


def _client(run_error=None, preview_error=None):
    calls = []

    class FakeService:
        async def run(self, req):
            calls.append(("run", req.me_user_id, req.partner_ref().describe(), req.turns))
            if run_error:
                raise run_error
            return _response()

        async def get(self, simulation_id):
            if simulation_id != "sim_demo":
                raise SimulationNotFound(simulation_id)
            return _response()

        async def get_report(self, simulation_id):
            if simulation_id != "sim_demo":
                raise SimulationNotFound(simulation_id)
            return REPORT

        async def list_for(self, ref):
            calls.append(("list", ref.describe()))
            return []

        async def preview_report(self, inp, use_llm):
            calls.append(("preview", inp.nickname_a, inp.nickname_b, use_llm))
            if preview_error:
                raise preview_error
            return ReportPreviewResponse(preview_id="prev_demo", report=REPORT)

        async def get_preview(self, preview_id):
            if preview_id != "prev_demo":
                raise ReportPreviewNotFound(preview_id)
            return ReportPreviewDetail(
                preview_id="prev_demo",
                persona_a=PersonaResponse(persona_id="pa", scores={}),
                persona_b=PersonaResponse(persona_id="pb", scores={}),
                nickname_a="A",
                nickname_b="B",
                transcript=Transcript(),
                use_llm=True,
                report=REPORT,
                created_at=REPORT.generated_at,
            )

        async def list_previews(self, limit):
            calls.append(("list_previews", limit))
            return [
                ReportPreviewSummary(
                    preview_id="prev_demo",
                    nickname_a="A",
                    nickname_b="B",
                    use_llm=True,
                    narrative_source=REPORT.narrative_source,
                    overall_score=REPORT.overall.score,
                    grade=REPORT.overall.grade,
                    grade_label=REPORT.overall.grade_label,
                    headline=REPORT.overall.headline,
                    created_at=REPORT.generated_at,
                )
            ]

    class FakeDb:
        async def commit(self):
            calls.append(("commit",))

        async def rollback(self):
            calls.append(("rollback",))

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_service] = lambda: FakeService()
    app.dependency_overrides[get_db] = lambda: FakeDb()
    return TestClient(app), calls


def test_run_returns_201_and_commits():
    client, calls = _client()

    res = client.post("/v1/simulation", json={"me_user_id": "u1", "partner_persona_id": "pb_002"})

    assert res.status_code == 201
    assert res.json()["simulation_id"] == "sim_demo"
    assert calls == [("run", "u1", "pb_002", 10), ("commit",)]


def test_run_without_partner_is_422():
    client, calls = _client()

    res = client.post("/v1/simulation", json={"me_user_id": "u1"})

    assert res.status_code == 422
    assert calls == []


def test_run_with_two_partner_refs_is_422():
    client, calls = _client()

    res = client.post(
        "/v1/simulation", json={"me_user_id": "u1", "partner_user_id": "u2", "partner_persona_id": "pb_002"}
    )

    assert res.status_code == 422
    assert calls == []


def test_run_turns_out_of_range_is_422():
    client, _ = _client()

    for turns in (2, 16):
        res = client.post("/v1/simulation", json={"me_user_id": "u1", "partner_user_id": "u2", "turns": turns})
        assert res.status_code == 422


def test_run_with_missing_persona_is_404_without_commit():
    client, calls = _client(run_error=PersonaNotFound("partner", PersonaRef(user_id="u2")))

    res = client.post("/v1/simulation", json={"me_user_id": "u1", "partner_user_id": "u2"})

    assert res.status_code == 404
    assert res.json()["detail"].startswith("partner: 확정된 페르소나가 없어요 (u2)")
    assert ("commit",) not in calls


def test_run_llm_failure_is_503_with_reason_and_rolls_back():
    client, calls = _client(run_error=SimulationFailed("timeout", reason="timeout"))

    res = client.post("/v1/simulation", json={"me_user_id": "u1", "partner_user_id": "u2"})

    assert res.status_code == 503
    assert res.json()["detail"] == {"message": "simulation failed: timeout", "reason": "timeout"}
    assert calls[-1] == ("rollback",)
    assert ("commit",) not in calls


def test_run_already_running_is_409():
    client, calls = _client(run_error=SimulationAlreadyRunning(frozenset({"pa", "pb"})))

    res = client.post("/v1/simulation", json={"me_user_id": "u1", "partner_user_id": "u2"})

    assert res.status_code == 409
    assert "이미 처리 중" in res.json()["detail"]
    assert ("commit",) not in calls


def test_list_with_no_ref_or_two_refs_has_query_specific_message():
    client, _ = _client()

    for params in ({}, {"user_id": "u1", "persona_id": "p1"}):
        res = client.get("/v1/simulation", params=params)
        assert res.status_code == 422
        assert res.json()["detail"] == "user_id 또는 persona_id 중 하나만 지정하세요"


def test_get_unknown_simulation_is_404():
    client, _ = _client()

    assert client.get("/v1/simulation/nope").status_code == 404
    assert client.get("/v1/simulation/nope/report").status_code == 404


def test_get_report_returns_stored_report():
    client, _ = _client()

    res = client.get("/v1/simulation/sim_demo/report")

    assert res.status_code == 200
    assert res.json()["overall"]["score"] == 62


def test_list_requires_exactly_one_of_user_or_persona():
    client, calls = _client()

    assert client.get("/v1/simulation").status_code == 422
    assert client.get("/v1/simulation?user_id=u1&persona_id=p1").status_code == 422
    assert client.get("/v1/simulation?user_id=u1").status_code == 200
    assert calls == [("list", "u1")]


def test_preview_passes_use_llm_to_service_and_returns_id_and_report():
    client, calls = _client()
    persona = {"persona_id": "pa", "scores": {}}

    res = client.post(
        "/v1/simulation/report/preview?use_llm=false",
        json={"persona_a": persona, "persona_b": persona, "nickname_a": "가상A", "nickname_b": "가상B"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["preview_id"] == "prev_demo"
    assert body["report"]["simulation_id"] == REPORT.simulation_id
    assert calls == [("preview", "가상A", "가상B", False), ("commit",)]


def test_get_report_preview_known_and_unknown():
    client, _ = _client()

    ok = client.get("/v1/simulation/report/preview/prev_demo")
    missing = client.get("/v1/simulation/report/preview/nope")

    assert ok.status_code == 200
    assert ok.json()["preview_id"] == "prev_demo"
    assert missing.status_code == 404


def test_list_report_previews_passes_limit():
    client, calls = _client()

    res = client.get("/v1/simulation/report/preview", params={"limit": 5})

    assert res.status_code == 200
    assert res.json()[0]["preview_id"] == "prev_demo"
    assert calls == [("list_previews", 5)]
