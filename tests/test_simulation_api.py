import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.features.persona.schemas import PersonaBrief, PersonaRef
from app.features.simulation import api
from app.features.simulation.agents import SimulationFailed
from app.features.simulation.schemas import MatchingReport, SimulationResponse, Turn
from app.features.simulation.service import PersonaNotFound, SimulationNotFound

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


def _client(run_error=None):
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
    assert res.json()["detail"].startswith("partner: 저장된 페르소나가 없어요 (u2)")
    assert ("commit",) not in calls


def test_run_llm_failure_is_503_and_rolls_back():
    client, calls = _client(run_error=SimulationFailed("timeout"))

    res = client.post("/v1/simulation", json={"me_user_id": "u1", "partner_user_id": "u2"})

    assert res.status_code == 503
    assert res.json()["detail"] == "simulation failed: timeout"
    assert calls[-1] == ("rollback",)
    assert ("commit",) not in calls


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


def test_preview_without_llm_uses_template_narrative():
    client, _ = _client()
    persona = {"persona_id": "pa", "scores": {}}

    res = client.post("/v1/simulation/report/preview?use_llm=false", json={"persona_a": persona, "persona_b": persona})

    assert res.status_code == 200
    assert res.json()["narrative_source"] == "template"
