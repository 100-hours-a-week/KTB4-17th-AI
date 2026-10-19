"""v2 시뮬레이션 서비스 — SQLite 에 진짜 repository, LLM 만 가짜.

POST 동기 검증(404·409·같은 상대 키), 백그라운드 실행 성공·실패 저장, stale 회수, 조회·목록.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.features.simulation.schemas import SimulationRequest
from app.v2.persona.models import UserPersonaCore, UserPersonaFact
from app.v2.simulation.models import V2Simulation, V2SimulationJob
from app.v2.simulation.service import (
    STALE_ERROR,
    JobInProgress,
    JobNotFound,
    PersonaNotFound,
    SimulationNotFound,
    SimulationV2Service,
)
from tests.conftest import seed_persona, with_db
from tests.v2.sim_fixtures import FakeLLM, good_script, judge_pass, report_call, script_call

SCORES = {"avoidance": 40, "contact_rhythm": 60, "seriousness": 70}


async def _seed(db):
    await seed_persona(
        db, persona_id="pa", user_id="u-a", nickname="민수", scores=SCORES, texts={"interests": ["등산하기"]}
    )
    await seed_persona(
        db, persona_id="pb", user_id="u-b", nickname="지은", scores=SCORES, texts={"interests": ["독서 모임"]}
    )


def _req(**kw):
    return SimulationRequest(**{"me_user_id": "u-a", "partner_user_id": "u-b", **kw})


def _ok_llm(script=None):
    return FakeLLM(
        {"generate": [script_call(script or good_script())], "judge": [judge_pass()], "report": [report_call()]}
    )


def test_start_requires_both_personas():
    async def scenario(sessions):
        async with sessions() as db:
            await seed_persona(db, persona_id="pa", user_id="u-a", nickname="민수")
            with pytest.raises(PersonaNotFound) as e:
                await SimulationV2Service(db).start(_req())
            assert e.value.who == "partner"
            assert (await db.execute(select(V2SimulationJob))).scalars().all() == []

    asyncio.run(with_db(scenario))


def test_start_conflict_same_partner_any_ref():
    async def scenario(sessions):
        async with sessions() as db:
            await _seed(db)
            job = await SimulationV2Service(db).start(_req())
            await db.commit()
            # 같은 상대를 persona_id 로 지정해도 같은 키 → 409
            with pytest.raises(JobInProgress) as e:
                await SimulationV2Service(db).start(_req(partner_user_id=None, partner_persona_id="pb"))
            assert e.value.job_id == job.id
            assert job.partner_key == "u-b" and job.partner_persona_id == "pb"

    asyncio.run(with_db(scenario))


def test_start_replaces_stale_active_job():
    async def scenario(sessions):
        async with sessions() as db:
            await _seed(db)
            svc = SimulationV2Service(db)
            old = await svc.start(_req())
            old.created_at = datetime.now(UTC) - timedelta(minutes=get_settings().simulation_v2_job_stale_minutes + 1)
            await db.commit()
            new = await svc.start(_req())
            await db.commit()
            assert new.id != old.id
            await db.refresh(old)
            assert old.status == "failed" and old.error == STALE_ERROR

    asyncio.run(with_db(scenario))


def test_run_success_saves_simulation_and_trace():
    model = get_settings().embedding_model_name
    e = [1.0] + [0.0] * 1023

    async def scenario(sessions):
        async with sessions() as db:
            await _seed(db)
            db.add_all(
                [
                    UserPersonaFact(
                        user_id="u-a",
                        category="취미",
                        fact="주말마다 산에 간다",
                        status="confirmed",
                        embedding=e,
                        embedding_model=model,
                    ),
                    UserPersonaFact(
                        user_id="u-b",
                        category="취미",
                        fact="트레킹 동호회 활동",
                        status="confirmed",
                        embedding=e,
                        embedding_model=model,
                    ),
                    UserPersonaCore(user_id="u-a", profile={"personality": "차분함", "job": "개발자"}),
                ]
            )
            await db.commit()
            job = await SimulationV2Service(db).start(_req())
            await db.commit()
            job_id = job.id

        script = good_script()
        script[2] = script[2].model_copy(update={"fact_refs": ["A-f1"]})
        llm = _ok_llm(script)
        async with sessions() as db:
            await SimulationV2Service(db, llm=llm).run(job_id)

        async with sessions() as db:
            svc = SimulationV2Service(db)
            job = await svc.get_job(job_id)
            assert job.status == "succeeded" and job.simulation_id and job.error is None
            assert job.stage == "reporting"
            sim = await db.get(V2Simulation, job.simulation_id)
            assert sim.mode == "shared"
            assert sim.transcript[2]["fact_refs"] == ["A-f1"]
            assert sim.topics_used[0]["fact_id"] and sim.topics_used[0]["text"] == "주말마다 산에 간다"
            assert {e["node"] for e in sim.harness_trace} >= {"plan_topics", "generate", "rule_check", "judge"}
            assert sim.report["simulation_id"] == sim.id

            resp = await svc.get(sim.id)
            assert resp.mode == "shared" and len(resp.transcript) == 20
            assert resp.transcript[0].speaker == "a" and resp.report.simulation_id == sim.id
            assert resp.me.nickname == "민수" and resp.partner.nickname == "지은"

            listed_me = await svc.list_for("u-a")
            listed_partner = await svc.list_for("u-b")
            assert [x.simulation_id for x in listed_me] == [sim.id] == [x.simulation_id for x in listed_partner]

        # 생성 프롬프트에 코어 성격은 들어가고 직업(민감)은 들어가지 않는다
        gen = next(c for c in llm.calls if c["name"] == "simulation-v2-generate")
        assert "차분함" in gen["user"] and "개발자" not in gen["user"]

    asyncio.run(with_db(scenario))


def test_run_failure_records_reason_and_no_simulation():
    bad = good_script()
    bad[2] = bad[2].model_copy(update={"text": "민수님은 주말에 뭐 하세요?"})

    async def scenario(sessions):
        async with sessions() as db:
            await _seed(db)
            job = await SimulationV2Service(db).start(_req())
            await db.commit()
            job_id = job.id
        llm = FakeLLM(
            {
                "generate": [script_call(bad)],
                "regenerate": [script_call(bad)],
                "repair": [script_call(bad)],
                "judge": [judge_pass()],
                "report": [report_call()],
            }
        )
        async with sessions() as db:
            await SimulationV2Service(db, llm=llm).run(job_id)
        async with sessions() as db:
            job = await SimulationV2Service(db).get_job(job_id)
            assert job.status == "failed" and job.error == "speaker_mixup_unresolved"
            assert job.simulation_id is None
            assert (await db.execute(select(V2Simulation))).scalars().all() == []

    asyncio.run(with_db(scenario))


def test_run_crash_marks_internal_error():
    async def boom(**kw):
        raise ZeroDivisionError

    async def scenario(sessions):
        async with sessions() as db:
            await _seed(db)
            job = await SimulationV2Service(db).start(_req())
            await db.commit()
            job_id = job.id
        async with sessions() as db:
            await SimulationV2Service(db, llm=boom).run(job_id)
        async with sessions() as db:
            job = await SimulationV2Service(db).get_job(job_id)
            assert job.status == "failed" and job.error == "internal_error"

    asyncio.run(with_db(scenario))


def test_run_ignores_non_pending_job():
    async def scenario(sessions):
        async with sessions() as db:
            await _seed(db)
            job = await SimulationV2Service(db).start(_req())
            job.status = "succeeded"
            await db.commit()
            job_id = job.id
        llm = _ok_llm()
        async with sessions() as db:
            await SimulationV2Service(db, llm=llm).run(job_id)
        assert llm.calls == []

    asyncio.run(with_db(scenario))


def test_get_job_marks_stale_and_not_found():
    async def scenario(sessions):
        async with sessions() as db:
            await _seed(db)
            svc = SimulationV2Service(db)
            job = await svc.start(_req())
            job.status = "running"
            job.started_at = datetime.now(UTC) - timedelta(hours=1)
            await db.commit()
            got = await svc.get_job(job.id)
            assert got.status == "failed" and got.error == STALE_ERROR
            with pytest.raises(JobNotFound):
                await svc.get_job("nope")
            with pytest.raises(SimulationNotFound):
                await svc.get("nope")

    asyncio.run(with_db(scenario))


def test_partner_by_session_without_facts_runs_solo_or_persona_only():
    async def scenario(sessions):
        async with sessions() as db:
            await _seed(db)
            job = await SimulationV2Service(db).start(_req(partner_user_id=None, partner_session_id="s-pb"))
            await db.commit()
            job_id = job.id
        async with sessions() as db:
            await SimulationV2Service(db, llm=_ok_llm()).run(job_id)
        async with sessions() as db:
            job = await SimulationV2Service(db).get_job(job_id)
            assert job.status == "succeeded"
            sim = await db.get(V2Simulation, job.simulation_id)
            assert sim.mode == "persona_only"

    asyncio.run(with_db(scenario))


def test_conflict_survives_partner_persona_new_version():
    """실측 버그: 카카오 추출로 상대 페르소나 새 버전이 생기면 옛 persona_id 로 부른 요청이 409 를 피해 갔다."""

    async def scenario(sessions):
        async with sessions() as db:
            await _seed(db)
            await seed_persona(db, persona_id="pb2", user_id="u-b", nickname="지은", scores=SCORES)  # 새 버전
            job = await SimulationV2Service(db).start(_req())  # user_id → 최신 pb2
            await db.commit()
            assert job.partner_persona_id in {"pb", "pb2"}
            with pytest.raises(JobInProgress):
                await SimulationV2Service(db).start(_req(partner_user_id=None, partner_persona_id="pb"))

    asyncio.run(with_db(scenario))
