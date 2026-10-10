from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import seed_persona, with_db
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.features.simulation_migration.models  # noqa: F401  테이블 등록
from app.core.db import get_db
from app.features.persona.schemas import Narrative, PersonaResponse
from app.features.simulation.service import SimulationService
from app.features.simulation_migration import api as migration_api
from app.features.simulation_migration.manager import RunManager
from app.features.simulation_migration.models import SimulationMigrationRun
from app.features.simulation_migration.repository import MigrationRepository
from app.features.simulation_migration.schemas import MigrationStartRequest
from app.main import app


def _make_persona(persona_id: str) -> PersonaResponse:
    return PersonaResponse(
        persona_id=persona_id,
        scores={},
        interests=["운동"],
        routine=["조깅"],
        narrative=Narrative(headline="운동가", body=f"{persona_id}본문", traits=["활발"]),
    )


class FakeGraph:
    """테스트용 가짜 LangGraph 스트림."""

    def __init__(self, events: list[dict[str, Any]] | None = None) -> None:
        self.events = events or []

    async def astream(self, initial_state: dict[str, Any]):
        for ev in self.events:
            yield ev


def test_existing_simulation_service_and_routes_preserved() -> None:
    """기존 SimulationService.run 및 라우트가 보존되어 있는지 검증."""
    assert hasattr(SimulationService, "run")
    client = TestClient(app)
    # 기존 시뮬레이션 목록 라우트 호출 (인자 누락 시 422 또는 200)
    res = client.get("/ai/api/v1/simulation_old")
    assert res.status_code in (200, 422)


def test_migration_start_ignores_extra_partner_fields() -> None:
    """상대는 partner_user_id 만 쓴다. persona/session 이 같이 오면 버리고, 그것만 있으면 422."""
    client = TestClient(app)
    missing = client.post(
        "/ai/api/v1/simulation",
        json={"me_user_id": "dummy-user-a", "partner_persona_id": "pb", "turns": 3},
    )
    assert missing.status_code == 422

    req = MigrationStartRequest.model_validate(
        {
            "me_user_id": "dummy-user-a",
            "partner_user_id": "dummy-user-b",
            "partner_persona_id": "pb",
            "partner_session_id": "s1",
            "turns": 3,
        }
    )
    assert req.me_user_id == "dummy-user-a"
    assert req.partner_user_id == "dummy-user-b"
    assert req.turns == 3
    assert "partner_persona_id" not in req.model_dump()
    assert "partner_session_id" not in req.model_dump()


def test_post_simulation_and_rate_limits_429() -> None:
    """POST 세 번째 동시 run은 429, 같은 user의 두 번째 run도 429 검증."""

    block_event = asyncio.Event()

    class BlockingGraph:
        async def astream(self, initial_state: dict[str, Any]):
            await block_event.wait()
            if False:
                yield {}

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="p1", user_id="u1", nickname="N1")
            await seed_persona(db, persona_id="p2", user_id="u2", nickname="N2")
            await seed_persona(db, persona_id="p3", user_id="u3", nickname="N3")
            await seed_persona(db, persona_id="p4", user_id="u4", nickname="N4")
            await seed_persona(db, persona_id="p5", user_id="u5", nickname="N5")
            await db.commit()

        # max_runs=2 인 manager 생성
        manager = RunManager(factory, max_runs=2, build_graph=lambda *a, **kw: BlockingGraph())

        async def override_get_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[migration_api.get_session_factory] = lambda: factory
        app.dependency_overrides[migration_api.get_run_manager] = lambda: manager

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # POST는 끝날 때까지 기다리므로, 진행 중 거절은 응답 전에 겹쳐서 보낸다.
                first = asyncio.create_task(
                    client.post(
                        "/ai/api/v1/simulation",
                        json={"me_user_id": "u1", "partner_user_id": "u2", "turns": 3},
                    )
                )
                second = asyncio.create_task(
                    client.post(
                        "/ai/api/v1/simulation",
                        json={"me_user_id": "u3", "partner_user_id": "u4", "turns": 3},
                    )
                )
                started = False
                for _ in range(40):
                    await asyncio.sleep(0.05)
                    async with factory() as db:
                        running = (
                            (
                                await db.execute(
                                    select(SimulationMigrationRun).where(SimulationMigrationRun.status == "running")
                                )
                            )
                            .scalars()
                            .all()
                        )
                    if len(running) >= 2:
                        started = True
                        break
                assert started

                res_same_user = await client.post(
                    "/ai/api/v1/simulation",
                    json={"me_user_id": "u1", "partner_user_id": "u3", "turns": 3},
                )
                assert res_same_user.status_code == 429

                res3 = await client.post(
                    "/ai/api/v1/simulation",
                    json={"me_user_id": "u5", "partner_user_id": "u4", "turns": 3},
                )
                assert res3.status_code == 429
                block_event.set()
                assert (await first).status_code == 503
                assert (await second).status_code == 503

        finally:
            block_event.set()
            await manager.shutdown()
            app.dependency_overrides.clear()

    asyncio.run(with_db(scenario))


def test_post_simulation_stale_pair_aborts_and_inserts_new() -> None:
    """같은 pair가 stale일 때만 aborted로 바꾼 뒤 새 행을 insert, fresh면 409 검증."""

    async def scenario(factory):
        now = datetime.now(UTC)
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")
            repo = MigrationRepository(db)
            # stale 행 생성 (heartbeat_at = now - 200s)
            stale_time = now - timedelta(seconds=200)
            stale_run = await repo.insert_run(
                persona_a_id="pa",
                persona_b_id="pb",
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            await repo.conditional_update(
                stale_run.id,
                stale_run.attempt,
                ["running"],
                status="running",
                heartbeat_at=stale_time,
            )
            await db.commit()

        manager = RunManager(factory, max_runs=2, build_graph=lambda *a, **kw: FakeGraph())

        async def override_get_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[migration_api.get_session_factory] = lambda: factory
        app.dependency_overrides[migration_api.get_run_manager] = lambda: manager

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # stale 상태이므로 기존 stale_run이 aborted로 바뀌고 새 run이 202로 시작됨
                res = await client.post(
                    "/ai/api/v1/simulation",
                    json={"me_user_id": "ua", "partner_user_id": "ub", "turns": 3},
                )
                assert res.status_code == 503
                new_sim_id = res.json()["detail"]["simulation_id"]
                assert new_sim_id != stale_run.id

                # 기존 stale 행이 DB에서 실제로 aborted로 바뀌었는지 확인
                async with factory() as db:
                    repo = MigrationRepository(db)
                    old_run = await repo.get_run(stale_run.id)
                    assert old_run.status == "aborted"

                # fresh 상태인 새 run이 진행 중일 때 다시 같은 pair로 요청하면 409
                res_fresh = await client.post(
                    "/ai/api/v1/simulation",
                    json={"me_user_id": "ua", "partner_user_id": "ub", "turns": 3},
                )
                # user_id_a 동일로 인한 429 또는 409
                assert res_fresh.status_code in (409, 429)

        finally:
            await manager.shutdown()
            app.dependency_overrides.clear()

    asyncio.run(with_db(scenario))


def test_resume_simulation_endpoint() -> None:
    """POST /{id}/resume 엔드포인트 동작 검증."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")
            repo = MigrationRepository(db)
            run = await repo.insert_run(
                persona_a_id="pa",
                persona_b_id="pb",
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            # failed 상태로 전이
            await repo.conditional_update(run.id, 1, ["running"], status="failed", error_reason="timeout")
            await db.commit()
            run_id = run.id

        manager = RunManager(factory, max_runs=2, build_graph=lambda *a, **kw: FakeGraph())

        async def override_get_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[migration_api.get_session_factory] = lambda: factory
        app.dependency_overrides[migration_api.get_run_manager] = lambda: manager

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # 1. 다른 me_user_id로 요청 -> 404
                res_wrong_user = await client.post(f"/ai/api/v1/simulation/{run_id}/resume?me_user_id=wrong_user")
                assert res_wrong_user.status_code == 404

                # 2. 필수 쿼리 누락 -> 422
                res_no_query = await client.post(f"/ai/api/v1/simulation/{run_id}/resume")
                assert res_no_query.status_code == 422

                # 3. 존재하지 않는 id -> 404
                res_not_found = await client.post("/ai/api/v1/simulation/not_exist_id/resume?me_user_id=ua")
                assert res_not_found.status_code == 404

                # 3. 올바른 재개 요청 -> 202
                res_ok = await client.post(f"/ai/api/v1/simulation/{run_id}/resume?me_user_id=ua")
                assert res_ok.status_code == 202

                # 4. 이미 running으로 전이된 후 다시 resume -> 409
                res_conflict = await client.post(f"/ai/api/v1/simulation/{run_id}/resume?me_user_id=ua")
                assert res_conflict.status_code == 409

        finally:
            await manager.shutdown()
            app.dependency_overrides.clear()

    asyncio.run(with_db(scenario))


def test_get_stale_does_not_modify_db_status() -> None:
    """GET stale은 DB status를 바꾸지 않고 표시 status만 aborted로 반환 검증."""

    async def scenario(factory):
        now = datetime.now(UTC)
        stale_time = now - timedelta(seconds=200)

        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")
            repo = MigrationRepository(db)
            run = await repo.insert_run(
                persona_a_id="pa",
                persona_b_id="pb",
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            await repo.conditional_update(
                run.id,
                1,
                ["running"],
                status="running",
                heartbeat_at=stale_time,
            )
            await db.commit()
            run_id = run.id

        async def override_get_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[migration_api.get_session_factory] = lambda: factory

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # GET 단건 조회
                res = await client.get(f"/ai/api/v1/simulation/{run_id}")
                assert res.status_code == 200
                data = res.json()
                # 응답 표시 status는 aborted
                assert data["status"] == "aborted"

                # DB 재확인: 실제 DB 컬럼은 running 유지, heartbeat_at 유지
                async with factory() as db:
                    repo = MigrationRepository(db)
                    saved = await repo.get_run(run_id)
                    assert saved.status == "running"

        finally:
            app.dependency_overrides.clear()

    asyncio.run(with_db(scenario))


def test_get_report_done_only() -> None:
    """GET /{id}/report는 done일 때만 200, 아니면 409 not_ready 검증."""
    import json
    from pathlib import Path

    report_data = json.loads(Path("tests/fixtures/matching_report.json").read_text())

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")
            repo = MigrationRepository(db)
            run = await repo.insert_run(
                persona_a_id="pa",
                persona_b_id="pb",
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            await db.commit()
            run_id = run.id

        async def override_get_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[migration_api.get_session_factory] = lambda: factory

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # 1. running 상태 -> 409 not_ready
                res_not_ready = await client.get(f"/ai/api/v1/simulation/{run_id}/report")
                assert res_not_ready.status_code == 409
                assert "not_ready" in res_not_ready.text

                # 2. done 및 report 저장
                async with factory() as db:
                    repo = MigrationRepository(db)

                    await repo.conditional_update(
                        run_id,
                        1,
                        ["running"],
                        status="done",
                        report=report_data,
                        narrative_source="llm",
                    )
                    await db.commit()

                # 3. done 상태 -> 200
                res_done = await client.get(f"/ai/api/v1/simulation/{run_id}/report")
                assert res_done.status_code == 200
                assert res_done.json()["simulation_id"] == run_id
                assert res_done.json()["narrative_source"] == "llm"

        finally:
            app.dependency_overrides.clear()

    asyncio.run(with_db(scenario))


def test_list_simulations() -> None:
    """GET "" 목록 조회 검증 (user_id 또는 persona_id 하나 필수)."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")
            repo = MigrationRepository(db)
            await repo.insert_run(
                persona_a_id="pa",
                persona_b_id="pb",
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            await db.commit()

        async def override_get_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[migration_api.get_session_factory] = lambda: factory

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # 1. 둘 다 없으면 422
                res_none = await client.get("/ai/api/v1/simulation")
                assert res_none.status_code == 422

                # 2. 둘 다 있으면 422
                res_both = await client.get("/ai/api/v1/simulation?user_id=ua&persona_id=pa")
                assert res_both.status_code == 422

                # 3. user_id로 조회 -> 200
                res_user = await client.get("/ai/api/v1/simulation?user_id=ua")
                assert res_user.status_code == 200
                assert len(res_user.json()) == 1

                # 4. persona_id로 조회 -> 200
                res_persona = await client.get("/ai/api/v1/simulation?persona_id=pb")
                assert res_persona.status_code == 200
                assert len(res_persona.json()) == 1

        finally:
            app.dependency_overrides.clear()

    asyncio.run(with_db(scenario))


def test_sse_events_stream_and_no_delta() -> None:
    """SSE에 delta 없음, index 0 후 1 커밋 시 수신, Last-Event-ID 0이면 1부터, 9번째 연결은 429 검증."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")
            repo = MigrationRepository(db)
            run = await repo.insert_run(
                persona_a_id="pa",
                persona_b_id="pb",
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            # index 0 대사 저장
            await repo.insert_utterance_if_owner(
                run_id=run.id,
                attempt=1,
                index=0,
                speaker="a",
                text="안녕하세요 0번",
            )
            await db.commit()
            run_id = run.id

        async def override_get_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[migration_api.get_session_factory] = lambda: factory

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # 1. 9번째 연결 시 429 검증
                migration_api._active_event_streams = 8
                res_429 = await client.get(f"/ai/api/v1/simulation/{run_id}/events")
                assert res_429.status_code == 429
                migration_api._active_event_streams = 0

                # 2. Last-Event-ID: 0 전달 시 index 1부터 받음
                # 미리 index 1 저장 및 done 전송
                async with factory() as db:
                    repo = MigrationRepository(db)
                    await repo.insert_utterance_if_owner(
                        run_id=run_id,
                        attempt=1,
                        index=1,
                        speaker="b",
                        text="반가워요 1번",
                    )
                    await repo.conditional_update(run_id, 1, ["running"], status="done")
                    await db.commit()

                res_sse = await client.get(
                    f"/ai/api/v1/simulation/{run_id}/events",
                    headers={"Last-Event-ID": "0"},
                )
                assert res_sse.status_code == 200
                content = res_sse.text

                # delta가 없음 확인
                assert "delta" not in content

                # index 0 은 없고 index 1이 포함됨 확인
                assert "안녕하세요 0번" not in content
                assert "반가워요 1번" in content
                assert "event: done" in content

        finally:
            app.dependency_overrides.clear()
            migration_api._active_event_streams = 0

    asyncio.run(with_db(scenario))


def test_task_runs_independent_of_client_disconnect() -> None:
    """생성 task는 클라이언트 연결과 독립적으로 끝까지 완료됨을 가짜 그래프로 검증."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")
            await db.commit()

        # 대사 2개를 차례대로 방출하고 report를 완료하는 가짜 그래프
        fake_events = [
            {"speak_a": {"transcript": [("a", "대사A")]}},
            {"speak_b": {"transcript": [("a", "대사A"), ("b", "대사B")]}},
            {"report": {"report": {"status": "good"}}},
        ]

        manager = RunManager(factory, max_runs=2, build_graph=lambda *a, **kw: FakeGraph(events=fake_events))

        async def override_get_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[migration_api.get_session_factory] = lambda: factory
        app.dependency_overrides[migration_api.get_run_manager] = lambda: manager

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.post(
                    "/ai/api/v1/simulation",
                    json={"me_user_id": "ua", "partner_user_id": "ub", "turns": 3},
                )
                assert res.status_code == 503
                sim_id = res.json()["detail"]["simulation_id"]

            # HTTP 클라이언트 연결은 이미 종료됨(disconnect).
            # 백그라운드 태스크 완료 대기
            await asyncio.sleep(0.3)

            # DB 상태 확인: 대사 2개가 저장되어 있음
            async with factory() as db:
                repo = MigrationRepository(db)
                run = await repo.get_run(sim_id)
                assert run is not None
                utts = await repo.list_utterances(sim_id)
                assert len(utts) == 2
                assert utts[0].text == "대사A"
                assert utts[1].text == "대사B"

        finally:
            await manager.shutdown()
            app.dependency_overrides.clear()

    asyncio.run(with_db(scenario))


def test_post_returns_transcript_and_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST는 끝날 때까지 기다렸다가 기존 시뮬레이션과 같은 모양으로 대화와 리포트를 준다."""
    import json
    from pathlib import Path

    report_data = json.loads(Path("tests/fixtures/matching_report.json").read_text())

    async def fake_report(state: Any) -> dict[str, Any]:
        return report_data

    monkeypatch.setattr(
        "app.features.simulation_migration.report_tool.write_matching_report",
        fake_report,
    )

    def build_graph(llm: Any = None, write_report: Any = None) -> Any:
        class CompletingGraph:
            async def astream(self, initial_state: dict[str, Any]):
                lines: list[tuple[str, str]] = []
                for index in range(6):
                    lines.append(("a" if index % 2 == 0 else "b", f"대사{index}"))
                    node = "speak_a" if index % 2 == 0 else "speak_b"
                    yield {node: {"transcript": list(lines)}}
                state = dict(initial_state)
                state["transcript"] = lines
                if write_report is not None:
                    await write_report(state)
                yield {"report": {}}

        return CompletingGraph()

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="민지")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="준호")
            await db.commit()

        manager = RunManager(factory, max_runs=2, build_graph=build_graph)

        async def override_get_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[migration_api.get_session_factory] = lambda: factory
        app.dependency_overrides[migration_api.get_run_manager] = lambda: manager

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.post(
                    "/ai/api/v1/simulation",
                    json={"me_user_id": "ua", "partner_user_id": "ub", "turns": 3},
                )
            assert res.status_code == 201
            body = res.json()
            assert body["turns"] == 3
            assert body["me"]["nickname"] == "민지"
            assert body["partner"]["nickname"] == "준호"
            assert [line["text"] for line in body["transcript"]] == [f"대사{i}" for i in range(6)]
            assert body["report"]["overall"]["score"] == 62
        finally:
            await manager.shutdown()
            app.dependency_overrides.clear()

    asyncio.run(with_db(scenario))


def test_lifespan_shutdown_and_sentinel_m1(monkeypatch: pytest.MonkeyPatch) -> None:
    """[M1] 라우트를 호출하지 않은 채 lifespan 종료 시 shutdown 동작 및 get_client().shutdown() sentinel 실행 보장 검증."""
    from app.main import app as main_app
    from app.main import lifespan

    sentinel_called = False
    manager_shutdown_called = False

    class DummyClient:
        def shutdown(self):
            nonlocal sentinel_called
            sentinel_called = True

    class DummyRunManager:
        async def shutdown(self):
            nonlocal manager_shutdown_called
            manager_shutdown_called = True
            # shutdown 중 예외 발생 시뮬레이션
            raise RuntimeError("Simulation of run_manager shutdown failure")

    monkeypatch.setattr("app.main.get_client", lambda: DummyClient())
    monkeypatch.setattr("app.main.get_run_manager", lambda: DummyRunManager())

    async def _test():
        nonlocal sentinel_called, manager_shutdown_called
        # lifespan 진입 후 종료
        with pytest.raises(RuntimeError, match="Simulation of run_manager shutdown failure"):
            async with lifespan(main_app):
                pass
        # manager shutdown이 불렸고, 에러에도 불구하고 sentinel(Langfuse client shutdown)이 불렸는지 검증
        assert manager_shutdown_called is True
        assert sentinel_called is True

    asyncio.run(_test())


def test_integrity_error_pair_and_user_unique_mapping_and_slot_release_m4() -> None:
    """[M4] partial unique 위반 시 pair_key는 409, user_a는 429로 매핑되고 슬롯이 정상 반납되는지 검증."""
    from sqlalchemy.exc import IntegrityError

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")

        # 1. pair unique 위반 시뮬레이션 (statement에 user_id_a와 pair_key가 둘 다 존재)
        class PairIntegrityManager(RunManager):
            async def start_run(self, *a, **kw):
                raise IntegrityError(
                    statement="INSERT INTO simulation_migration_runs (id, user_id_a, pair_key) VALUES (?, ?, ?)",
                    params={},
                    orig=Exception("UNIQUE constraint failed: uq_simulation_migration_runs_pair_active"),
                )

        pair_mgr = PairIntegrityManager(factory, max_runs=5)

        async def override_get_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[migration_api.get_session_factory] = lambda: factory
        app.dependency_overrides[migration_api.get_run_manager] = lambda: pair_mgr

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                res_pair = await client.post(
                    "/ai/api/v1/simulation",
                    json={"me_user_id": "ua", "partner_user_id": "ub", "turns": 3},
                )
                assert res_pair.status_code == 409
                assert (
                    "uq_simulation_migration_runs_pair_active" in res_pair.json()["detail"]
                    or "동일 페르소나 쌍" in res_pair.json()["detail"]
                )
                # 슬롯이 반납되어 0인지 확인
                assert pair_mgr._active_slots == 0

            # 2. user_a unique 위반 시뮬레이션 (statement에 user_id_a와 pair_key가 둘 다 존재)
            class UserIntegrityManager(RunManager):
                async def start_run(self, *a, **kw):
                    raise IntegrityError(
                        statement="INSERT INTO simulation_migration_runs (id, user_id_a, pair_key) VALUES (?, ?, ?)",
                        params={},
                        orig=Exception("UNIQUE constraint failed: uq_simulation_migration_runs_user_a_active"),
                    )

            user_mgr = UserIntegrityManager(factory, max_runs=5)
            app.dependency_overrides[migration_api.get_run_manager] = lambda: user_mgr

            async with AsyncClient(transport=transport, base_url="http://test") as client:
                res_user = await client.post(
                    "/ai/api/v1/simulation",
                    json={"me_user_id": "ua", "partner_user_id": "ub", "turns": 3},
                )
                assert res_user.status_code == 429
                assert (
                    "uq_simulation_migration_runs_user_a_active" in res_user.json()["detail"]
                    or "동일 사용자의 활성 시뮬레이션" in res_user.json()["detail"]
                )
                # 슬롯이 반납되어 0인지 확인
                assert user_mgr._active_slots == 0

        finally:
            app.dependency_overrides.clear()

    asyncio.run(with_db(scenario))


def test_resume_integrity_error_does_not_double_release_slot() -> None:
    """[3] resume 실행 도중 IntegrityError가 발생해도 슬롯을 이중 반납하지 않아 기존 _active_slots(1)가 그대로 유지되는지 검증."""
    from sqlalchemy.exc import IntegrityError

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")
            repo = MigrationRepository(db)
            run = await repo.insert_run(
                persona_a_id="pa",
                persona_b_id="pb",
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            await repo.conditional_update(run.id, 1, ["running"], status="failed", error_reason="timeout")
            await db.commit()
            run_id = run.id

        class FailingResumeManager(RunManager):
            async def resume(self, *a, **kw):
                # 1. manager.resume 내부 동작대로 슬롯 획득 시뮬레이션
                if not kw.get("slot_acquired", False):
                    self.acquire_run_slot()
                try:
                    raise IntegrityError(
                        statement="UPDATE simulation_migration_runs SET ... user_id_a ... pair_key ...",
                        params={},
                        orig=Exception("UNIQUE constraint failed: uq_simulation_migration_runs_pair_active"),
                    )
                except Exception:
                    # manager.resume 내부의 슬롯 반납 (자기가 얻은 것만 반납)
                    self.release_run_slot()
                    raise

        manager = FailingResumeManager(factory, max_runs=5)
        # 다른 작업이 이미 슬롯 1개를 쥐고 있는 상태로 설정
        manager.acquire_run_slot()
        assert manager._active_slots == 1

        async def override_get_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[migration_api.get_session_factory] = lambda: factory
        app.dependency_overrides[migration_api.get_run_manager] = lambda: manager

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.post(f"/ai/api/v1/simulation/{run_id}/resume?me_user_id=ua")
                assert res.status_code == 409

            # 이중 반납되지 않아 원래 쥐고 있던 슬롯 1개가 유지되어야 함!
            assert manager._active_slots == 1
        finally:
            manager.release_run_slot()
            app.dependency_overrides.clear()

    asyncio.run(with_db(scenario))


def test_get_events_fresh_heartbeat_not_aborted_m4() -> None:
    """[m4] /events 진입 시 fresh한 heartbeat를 가진 run은 abort_stale에 의해 aborted로 덮어쓰이지 않는지 검증."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")
            repo = MigrationRepository(db)
            run = await repo.insert_run(
                persona_a_id="pa",
                persona_b_id="pb",
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            # 완료로 만들어 스트림이 즉시 끝나게 함
            await repo.conditional_update(run.id, 1, ["running"], status="done")
            await db.commit()
            run_id = run.id

        async def override_get_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[migration_api.get_session_factory] = lambda: factory

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.get(f"/ai/api/v1/simulation/{run_id}/events")
                assert res.status_code == 200

            # DB 상태 확인: aborted가 아니라 done 유지
            async with factory() as db:
                repo = MigrationRepository(db)
                r = await repo.get_run(run_id)
                assert r is not None
                assert r.status == "done"
        finally:
            app.dependency_overrides.clear()

    asyncio.run(with_db(scenario))


def test_get_events_abort_stale_false_does_not_emit_error_aborted_n1(monkeypatch: pytest.MonkeyPatch) -> None:
    """[N1] /events 폴링 중 abort_stale이 False일 때 (경쟁으로 다른 세션이 done 커밋) error: aborted로 닫히지 않고 다음 폴링에서 done 이벤트를 정상 전달하는지 검증."""

    async def scenario(factory):
        now = datetime.now(UTC)
        stale_time = now - timedelta(seconds=200)

        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")
            repo = MigrationRepository(db)
            run = await repo.insert_run(
                persona_a_id="pa",
                persona_b_id="pb",
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            # stale 상태로 설정
            run.heartbeat_at = stale_time
            await db.commit()
            run_id = run.id

        # get_events 내부의 1초 대기를 0.01초로 단축하여 테스트 고속화
        real_sleep = asyncio.sleep

        async def fast_sleep(_delay):
            await real_sleep(0.01)

        monkeypatch.setattr("app.features.simulation_migration.api.asyncio.sleep", fast_sleep)

        # 경쟁 상황 시뮬레이션:
        # get_events의 첫 번째 폴링에서 abort_stale이 호출되기 직전에 다른 세션에서 done으로 커밋
        original_abort_stale = MigrationRepository.abort_stale
        first_call = True

        async def mocked_abort_stale(self, r_id, *args, **kwargs):
            nonlocal first_call
            if first_call and r_id == run_id:
                first_call = False
                # 세션 B에서 done으로 커밋하여 경쟁 발생 유도
                async with factory() as db_compete:
                    repo_compete = MigrationRepository(db_compete)
                    await repo_compete.conditional_update(run_id, 1, ["running"], status="done")
                    await db_compete.commit()
            return await original_abort_stale(self, r_id, *args, **kwargs)

        monkeypatch.setattr(MigrationRepository, "abort_stale", mocked_abort_stale)

        async def override_get_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[migration_api.get_session_factory] = lambda: factory

        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                res = await client.get(f"/ai/api/v1/simulation/{run_id}/events")
                assert res.status_code == 200
                content = res.text

                # error: aborted 이벤트가 절대 포함되지 않아야 함
                assert "event: error" not in content
                assert "aborted" not in content
                # 다음 폴링에서 정상적으로 event: done 수신
                assert "event: done" in content

        finally:
            app.dependency_overrides.clear()
            migration_api._active_event_streams = 0

    asyncio.run(with_db(scenario))
