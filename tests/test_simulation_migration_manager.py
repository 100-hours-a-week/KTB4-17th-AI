"""시뮬레이션 마이그레이션 RunManager 단위 테스트."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import seed_persona, with_db
from sqlalchemy.exc import IntegrityError

import app.features.simulation_migration.models  # noqa: F401  Base.metadata에 테이블 등록
from app.features.persona.schemas import Narrative, PersonaResponse
from app.features.simulation_migration.manager import ConflictError, RunManager, SlotUnavailable
from app.features.simulation_migration.repository import MigrationRepository


def _make_personas() -> tuple[PersonaResponse, PersonaResponse]:
    p_a = PersonaResponse(
        persona_id="p-alpha",
        scores={},
        interests=["운동"],
        routine=["아침 운동"],
        narrative=Narrative(headline="운동가", body="운동본문", traits=["활발"]),
    )
    p_b = PersonaResponse(
        persona_id="p-beta",
        scores={},
        interests=["독서"],
        routine=["저녁 독서"],
        narrative=Narrative(headline="독서가", body="독서본문", traits=["차분"]),
    )
    return p_a, p_b


class FakeGraph:
    """테스트용 가짜 컴파일된 그래프."""

    def __init__(self, events: list[dict[str, Any]] | None = None, raise_exc: Exception | None = None) -> None:
        self.events = events or []
        self.raise_exc = raise_exc

    async def astream(self, initial_state: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        if self.raise_exc:
            raise self.raise_exc
        for ev in self.events:
            yield ev


def test_slot_limit_third_acquire_is_false() -> None:
    """슬롯 상한 2. 세 번째 acquire는 False이고, release 후 다시 획득 가능하다."""

    async def scenario(factory):
        manager = RunManager(factory, max_runs=2)
        assert manager.acquire_run_slot() is True
        assert manager.acquire_run_slot() is True
        assert manager.acquire_run_slot() is False
        manager.release_run_slot()
        assert manager.acquire_run_slot() is True

    asyncio.run(with_db(scenario))


def test_stale_and_fresh_abort_if_stale() -> None:
    """stale이 아닌 행은 abort_if_stale이 False이고 status가 유지된다. stale 행은 True이고 aborted로 전이된다."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="p-alpha", user_id="u-a", nickname="A")
            await seed_persona(db, persona_id="p-beta", user_id="u-b", nickname="B")
            await seed_persona(db, persona_id="p-gamma", user_id="u-c", nickname="C")
            await seed_persona(db, persona_id="p-delta", user_id="u-d", nickname="D")
            repo = MigrationRepository(db)

            now = datetime.now(UTC)
            # 1. Fresh run
            fresh_run = await repo.insert_run(
                persona_a_id="p-alpha",
                persona_b_id="p-beta",
                user_id_a="u-a",
                user_id_b="u-b",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            fresh_id = fresh_run.id

            # 2. Stale run
            stale_run = await repo.insert_run(
                persona_a_id="p-gamma",
                persona_b_id="p-delta",
                user_id_a="u-c",
                user_id_b="u-d",
                nickname_a="C",
                nickname_b="D",
                turns=3,
            )
            stale_id = stale_run.id
            stale_run.heartbeat_at = now - timedelta(seconds=200)
            await db.commit()

        manager = RunManager(factory, stale_s=120.0)

        # Fresh 검증
        fresh_aborted = await manager.abort_if_stale(fresh_id)
        assert fresh_aborted is False

        async with factory() as db:
            repo = MigrationRepository(db)
            r_fresh = await repo.get_run(fresh_id)
            assert r_fresh is not None
            assert r_fresh.status == "running"

        # Stale 검증
        stale_aborted = await manager.abort_if_stale(stale_id)
        assert stale_aborted is True

        async with factory() as db:
            repo = MigrationRepository(db)
            r_stale = await repo.get_run(stale_id)
            assert r_stale is not None
            assert r_stale.status == "aborted"
            assert r_stale.error_reason == "aborted"

    asyncio.run(with_db(scenario))


def test_wrong_attempt_conditional_update_and_utterance_rejected() -> None:
    """잘못된 attempt의 갱신은 status를 바꾸지 않고 utterance를 넣지 않는다."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="p-alpha", user_id="u-a", nickname="A")
            await seed_persona(db, persona_id="p-beta", user_id="u-b", nickname="B")
            repo = MigrationRepository(db)
            run = await repo.insert_run(
                persona_a_id="p-alpha",
                persona_b_id="p-beta",
                user_id_a="u-a",
                user_id_b="u-b",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            run_id = run.id
            await db.commit()

            # 1. attempt 불일치 conditional_update
            updated = await repo.conditional_update(
                run_id,
                999,
                ["running"],
                status="reporting",
            )
            assert updated is False

            r = await repo.get_run(run_id)
            assert r is not None
            assert r.status == "running"

            # 2. attempt 불일치 insert_utterance_if_owner
            res = await repo.insert_utterance_if_owner(
                run_id,
                attempt=999,
                index=0,
                speaker="a",
                text="안녕하세요",
            )
            assert res == "lost"

            utterances = await repo.list_utterances(run_id)
            assert utterances == []

    asyncio.run(with_db(scenario))


def test_task_exception_sets_status_failed_and_error_task_error() -> None:
    """task 예외는 failed / task_error다."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="p-alpha", user_id="u-a", nickname="A")
            await seed_persona(db, persona_id="p-beta", user_id="u-b", nickname="B")

        pa, pb = _make_personas()

        def build_broken_graph(*args: Any, **kwargs: Any) -> FakeGraph:
            return FakeGraph(raise_exc=RuntimeError("그래프 비정상 오류"))

        manager = RunManager(factory, max_runs=2, build_graph=build_broken_graph)
        task = await manager.start_run(
            persona_a=pa,
            persona_b=pb,
            user_id_a="u-a",
            user_id_b="u-b",
            nickname_a="A",
            nickname_b="B",
            turns=3,
        )
        run_id = task.run_id

        with pytest.raises(RuntimeError):
            await task

        # 짧은 이벤트 루프 대기로 done callback 완료 보장
        await asyncio.sleep(0.05)

        async with factory() as db:
            repo = MigrationRepository(db)
            r = await repo.get_run(run_id)
            assert r is not None
            assert r.status == "failed"
            assert r.error_reason == "task_error"

    asyncio.run(with_db(scenario))


def test_lifespan_cancellation_sets_aborted_not_failed() -> None:
    """lifespan 취소(shutdown)는 aborted이고 failed가 아니다."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="p-alpha", user_id="u-a", nickname="A")
            await seed_persona(db, persona_id="p-beta", user_id="u-b", nickname="B")

        pa, pb = _make_personas()
        block_event = asyncio.Event()

        class BlockingGraph:
            async def astream(self, initial_state: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
                await block_event.wait()
                if False:
                    yield {}

        manager = RunManager(factory, max_runs=2, build_graph=lambda *a, **kw: BlockingGraph())
        task = await manager.start_run(
            persona_a=pa,
            persona_b=pb,
            user_id_a="u-a",
            user_id_b="u-b",
            nickname_a="A",
            nickname_b="B",
            turns=3,
        )
        run_id = task.run_id

        # 작업 진행 중 shutdown 호출
        await manager.shutdown()

        async with factory() as db:
            repo = MigrationRepository(db)
            r = await repo.get_run(run_id)
            assert r is not None
            assert r.status == "aborted"
            assert r.error_reason == "aborted"
            assert r.status != "failed"

    asyncio.run(with_db(scenario))


def test_concurrent_resume_allows_only_one_running_and_second_fails() -> None:
    """resume 두 번 동시 시작은 진행 중 행이 하나다. 두 번째는 슬롯 또는 조건부 갱신 실패다."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="p-alpha", user_id="u-a", nickname="A")
            await seed_persona(db, persona_id="p-beta", user_id="u-b", nickname="B")
            repo = MigrationRepository(db)
            run = await repo.insert_run(
                persona_a_id="p-alpha",
                persona_b_id="p-beta",
                user_id_a="u-a",
                user_id_b="u-b",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            run_id = run.id
            # failed 상태로 설정
            await repo.conditional_update(run_id, 1, ["running"], status="failed", error_reason="test")
            await db.commit()

        pa, pb = _make_personas()
        block_event = asyncio.Event()

        class BlockingGraph:
            async def astream(self, initial_state: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
                await block_event.wait()
                if False:
                    yield {}

        manager = RunManager(factory, max_runs=2, build_graph=lambda *a, **kw: BlockingGraph())

        results = await asyncio.gather(
            manager.resume(run_id, persona_a=pa, persona_b=pb),
            manager.resume(run_id, persona_a=pa, persona_b=pb),
            return_exceptions=True,
        )

        successful_tasks = [r for r in results if isinstance(r, asyncio.Task)]
        failed_results = [r for r in results if isinstance(r, Exception)]

        assert len(successful_tasks) == 1
        assert len(failed_results) == 1
        assert isinstance(failed_results[0], (SlotUnavailable, ConflictError))

        # 성공한 task 정리
        await asyncio.sleep(0.01)
        await manager.shutdown()

    asyncio.run(with_db(scenario))


def test_cannot_enter_reporting_before_utterances_are_full() -> None:
    """대사가 가득 차기 전에 reporting으로 가지 않는다."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="p-alpha", user_id="u-a", nickname="A")
            await seed_persona(db, persona_id="p-beta", user_id="u-b", nickname="B")
            repo = MigrationRepository(db)
            run = await repo.insert_run(
                persona_a_id="p-alpha",
                persona_b_id="p-beta",
                user_id_a="u-a",
                user_id_b="u-b",
                nickname_a="A",
                nickname_b="B",
                turns=3,  # 3왕복 -> 6대사 필요
            )
            run_id = run.id
            # 2개 대사만 삽입
            await repo.insert_utterance_if_owner(run_id, 1, 0, "a", "대사 0")
            await repo.insert_utterance_if_owner(run_id, 1, 1, "b", "대사 1")
            await db.commit()

        pa, pb = _make_personas()

        # report 노드만 발생시키는 가짜 그래프 (아직 2대사뿐인데 report 시도)
        class PrematureReportGraph:
            async def astream(self, initial_state: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
                # report 이벤트 발생
                yield {"report": {"report": {"score": 100}}}

        manager = RunManager(factory, max_runs=2, build_graph=lambda *a, **kw: PrematureReportGraph())

        # 직접 _execute_run 호출하여 동작 확인
        await manager._execute_run(
            run_id=run_id,
            attempt=1,
            persona_a=pa,
            persona_b=pb,
            nickname_a="A",
            nickname_b="B",
            turns=3,
        )

        async with factory() as db:
            repo = MigrationRepository(db)
            r = await repo.get_run(run_id)
            assert r is not None
            # reporting으로 가지 않고 여전히 running 유지
            assert r.status == "running"

    asyncio.run(with_db(scenario))


def test_start_run_insert_fails_releases_slot_and_no_task() -> None:
    """insert_run 실패 시 task를 만들지 않고 슬롯을 반환한다."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="p-alpha", user_id="u-a", nickname="A")
            await seed_persona(db, persona_id="p-beta", user_id="u-b", nickname="B")
            repo = MigrationRepository(db)
            # 첫 번째 running 실행 생성
            await repo.insert_run(
                persona_a_id="p-alpha",
                persona_b_id="p-beta",
                user_id_a="u-a",
                user_id_b="u-b",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            await db.commit()

        pa, pb = _make_personas()
        manager = RunManager(factory, max_runs=2)

        # 동일 pair_key로 start_run 시도 -> IntegrityError 발생
        with pytest.raises(IntegrityError):
            await manager.start_run(
                persona_a=pa,
                persona_b=pb,
                user_id_a="u-a",
                user_id_b="u-b",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )

        # 슬롯이 반환되어 active_slots == 0인지 확인
        assert manager._active_slots == 0
        # task가 생성되지 않았는지 확인
        assert len(manager._tasks) == 0

    asyncio.run(with_db(scenario))


def test_full_run_with_utterances_and_report_done() -> None:
    """정상 시뮬레이션: 발화 저장 후 reporting 전이 및 done 완료 검증."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="p-alpha", user_id="u-a", nickname="A")
            await seed_persona(db, persona_id="p-beta", user_id="u-b", nickname="B")

        pa, pb = _make_personas()

        # 2턴(4대사)을 순차적으로 생성하는 가짜 그래프
        events = [
            {"speak_a": {"transcript": [("a", "안녕 알파")]}},
            {"speak_b": {"transcript": [("a", "안녕 알파"), ("b", "안녕 베타")]}},
            {"speak_a": {"transcript": [("a", "안녕 알파"), ("b", "안녕 베타"), ("a", "반가워")]}},
            {
                "speak_b": {
                    "transcript": [("a", "안녕 알파"), ("b", "안녕 베타"), ("a", "반가워"), ("b", "나도 반가워")]
                }
            },
        ]

        report_called = False

        async def mock_write_report(state: Any) -> dict[str, Any]:
            nonlocal report_called
            report_called = True
            return {"summary": "리포트 완료", "source": "llm"}

        def custom_build_graph(llm: Any = None, write_report: Any = None) -> FakeGraph:
            class MockStateGraph:
                async def astream(self, initial_state: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
                    for ev in events:
                        yield ev
                    # 마지막에 write_report 호출
                    if write_report is not None:

                        class FakeState:
                            transcript = [
                                ("a", "안녕 알파"),
                                ("b", "안녕 베타"),
                                ("a", "반가워"),
                                ("b", "나도 반가워"),
                            ]

                        rep = await write_report(FakeState())
                        yield {"report": {"report": rep}}

            return MockStateGraph()  # type: ignore[return-value]

        manager = RunManager(
            factory,
            max_runs=2,
            build_graph=custom_build_graph,
            report_heartbeat_interval_s=0.1,
        )

        task = await manager.start_run(
            persona_a=pa,
            persona_b=pb,
            user_id_a="u-a",
            user_id_b="u-b",
            nickname_a="A",
            nickname_b="B",
            turns=2,
            write_report=mock_write_report,
        )
        run_id = task.run_id

        await task
        assert report_called is True

        async with factory() as db:
            repo = MigrationRepository(db)
            r = await repo.get_run(run_id)
            assert r is not None
            assert r.status == "done"
            assert r.narrative_source == "llm"

            utterances = await repo.list_utterances(run_id)
            assert len(utterances) == 4
            assert [u.text for u in utterances] == [
                "안녕 알파",
                "안녕 베타",
                "반가워",
                "나도 반가워",
            ]

    asyncio.run(with_db(scenario))


def test_resume_persona_unavailable_keeps_status_releases_slot_m3() -> None:
    """[M3] resume 시 persona가 DB에 없으면(persona_unavailable) 상태/attempt 전이 없이 409 및 슬롯 반납 검증."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="p-alpha", user_id="u-a", nickname="A")
            # p-beta는 seed하지 않음!
            repo = MigrationRepository(db)
            run = await repo.insert_run(
                persona_a_id="p-alpha",
                persona_b_id="p-beta",
                user_id_a="u-a",
                user_id_b="u-b",
                nickname_a="A",
                nickname_b="B",
                turns=3,
            )
            # failed 상태로 설정
            await repo.conditional_update(run.id, 1, ["running"], status="failed", error_reason="timeout")
            await db.commit()
            run_id = run.id

        manager = RunManager(factory, max_runs=2, build_graph=lambda *a, **kw: FakeGraph())

        # persona_a, persona_b를 명시적으로 주지 않아 DB 조회가 일어나도록 함
        with pytest.raises(ConflictError, match="persona_unavailable"):
            await manager.resume(run_id, persona_a=None, persona_b=None)

        # 1. 슬롯이 정상 반납되었는지 검증
        assert manager._active_slots == 0

        # 2. DB 상태 및 attempt가 변경되지 않고 그대로 유지되었는지 검증
        async with factory() as db:
            repo = MigrationRepository(db)
            r = await repo.get_run(run_id)
            assert r.status == "failed"
            assert r.attempt == 1
            assert r.error_reason == "timeout"

    asyncio.run(with_db(scenario))


def test_registry_tuple_key_attempt_isolation_m6() -> None:
    """[m6] 동일 run_id의 서로 다른 attempt가 (run_id, attempt) 튜플 키로 격리되어 앞선 attempt의 완료가 뒤의 등록을 지우지 않는지 검증."""

    async def scenario(factory):
        manager = RunManager(factory, max_runs=5)

        # fake tasks
        loop = asyncio.get_running_loop()
        f1 = loop.create_future()
        f2 = loop.create_future()

        async def dummy_coro(fut):
            await fut

        t1 = asyncio.create_task(dummy_coro(f1))
        t2 = asyncio.create_task(dummy_coro(f2))

        manager.acquire_run_slot()
        manager._register_task("run-123", 1, t1)

        manager.acquire_run_slot()
        manager._register_task("run-123", 2, t2)

        # 튜플 키 검증
        assert ("run-123", 1) in manager._registered_runs
        assert ("run-123", 2) in manager._registered_runs
        assert manager._registered_runs[("run-123", 1)].task is t1
        assert manager._registered_runs[("run-123", 2)].task is t2

        # attempt 1 완료 처리
        f1.set_result(True)
        await asyncio.sleep(0.01)

        # attempt 1은 pop되었지만, attempt 2는 그대로 남아있어야 함
        assert ("run-123", 1) not in manager._registered_runs
        assert ("run-123", 2) in manager._registered_runs

        # attempt 2 정리
        f2.set_result(True)
        await asyncio.sleep(0.01)
        assert ("run-123", 2) not in manager._registered_runs

    asyncio.run(with_db(scenario))


def test_start_run_slot_acquired_flag_m7() -> None:
    """[m7] start_run에서 slot_acquired=True이면 실패 시 슬롯을 반납하지 않고, slot_acquired=False이면 실패 시 내부 획득 슬롯을 반납하는지 검증."""

    async def scenario(factory):
        pa, pb = _make_personas()

        # 실패하는 session_factory
        class FailingFactory:
            def __call__(self):
                raise RuntimeError("DB connection failure")

        manager = RunManager(FailingFactory(), max_runs=2)

        # 1. slot_acquired=False (기본): 내부에서 슬롯 획득 후 DB 실패 시 반납해야 함
        assert manager._active_slots == 0
        with pytest.raises(RuntimeError):
            await manager.start_run(
                persona_a=pa,
                persona_b=pb,
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
                slot_acquired=False,
            )
        assert manager._active_slots == 0

        # 2. slot_acquired=True: 외부에서 슬롯을 이미 획득한 상태로 진입 시 DB 실패해도 내부에서 release하지 않아야 함
        manager.acquire_run_slot()
        assert manager._active_slots == 1
        with pytest.raises(RuntimeError):
            await manager.start_run(
                persona_a=pa,
                persona_b=pb,
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
                slot_acquired=True,
            )
        # 외부에서 획득한 슬롯이므로 내부에서 줄이지 않아 1 유지
        assert manager._active_slots == 1
        manager.release_run_slot()
        assert manager._active_slots == 0

    asyncio.run(with_db(scenario))


def test_report_heartbeat_zero_rowcount_cancels_report_task_m8() -> None:
    """[m8] 리포트 단계에서 heartbeat 갱신 rowcount가 0이면(다른 곳에서 실패/취소) write_task가 취소되는지 검증."""

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="p-alpha", user_id="u-a", nickname="A")
            await seed_persona(db, persona_id="p-beta", user_id="u-b", nickname="B")

        pa, pb = _make_personas()

        # write_report가 영원히 대기하는 동안 외부에서 DB 상태를 aborted로 변경
        report_cancelled = False

        async def hanging_write_report(state: Any) -> dict[str, Any]:
            nonlocal report_cancelled
            try:
                await asyncio.sleep(10)
                return {"summary": "done", "source": "llm"}
            except asyncio.CancelledError:
                report_cancelled = True
                raise

        def custom_build_graph(llm: Any = None, write_report: Any = None) -> FakeGraph:
            class MockStateGraph:
                async def astream(self, initial_state: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
                    # 즉시 reporting 진입을 위해 transcript 4개와 report 요청
                    yield {
                        "speak_b": {
                            "transcript": [("a", "1"), ("b", "2"), ("a", "3"), ("b", "4")],
                        }
                    }
                    if write_report is not None:
                        rep = await write_report({"transcript": [("a", "1"), ("b", "2"), ("a", "3"), ("b", "4")]})
                        yield {"report": {"report": rep}}

            return MockStateGraph()  # type: ignore[return-value]

        manager = RunManager(
            factory,
            max_runs=2,
            build_graph=custom_build_graph,
            report_heartbeat_interval_s=0.05,
        )

        task = await manager.start_run(
            persona_a=pa,
            persona_b=pb,
            user_id_a="u-a",
            user_id_b="u-b",
            nickname_a="A",
            nickname_b="B",
            turns=2,
            write_report=hanging_write_report,
        )
        run_id = task.run_id

        # reporting 상태에 도달할 때까지 잠시 대기
        for _ in range(50):
            await asyncio.sleep(0.02)
            async with factory() as db:
                repo = MigrationRepository(db)
                r = await repo.get_run(run_id)
                if r and r.status == "reporting":
                    break

        # 외부에서 상태를 failed로 덮어써서 heartbeat rowcount가 0이 되도록 유도
        async with factory() as db:
            repo = MigrationRepository(db)
            await repo.conditional_update(run_id, 1, ["reporting"], status="failed", error_reason="external_cancel")
            await db.commit()

        # 태스크 완료 대기
        await task

        # write_task가 취소되었는지 확인
        assert report_cancelled is True

    asyncio.run(with_db(scenario))


def test_before_request_db_error_results_in_db_error_status_m8() -> None:
    """[m8] before_request 중 DB 예외 발생 시 upstream_error가 아닌 db_error로 실패하는지 검증."""
    from app.features.simulation_migration.llm import LLMError

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="p-alpha", user_id="u-a", nickname="A")
            await seed_persona(db, persona_id="p-beta", user_id="u-b", nickname="B")

        pa, pb = _make_personas()
        real_factory = factory

        class FlakyFactory:
            def __init__(self):
                self.fail_before_request = False

            def __call__(self):
                if self.fail_before_request:
                    raise RuntimeError("Simulated DB connection failure in before_request")
                return real_factory()

        flaky_factory = FlakyFactory()

        # speak_a가 실행될 때 before_request를 호출하도록 유도하는 커스텀 그래프
        def custom_build_graph(llm: Any = None, write_report: Any = None) -> FakeGraph:
            class MockStateGraph:
                async def astream(self, initial_state: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
                    # before_req 호출 시 플래그 켜서 DB 실패 유도
                    flaky_factory.fail_before_request = True
                    try:
                        await llm.before_req()
                        err_reason = "none"
                    except LLMError as e:
                        err_reason = e.reason
                    finally:
                        flaky_factory.fail_before_request = False

                    # LLMError(reason="db_error")가 발생했는지 확인
                    assert err_reason == "db_error"
                    yield {"speak_a": {"error": err_reason}}

            return MockStateGraph()  # type: ignore[return-value]

        manager = RunManager(
            flaky_factory,
            max_runs=2,
            build_graph=custom_build_graph,
        )

        task = await manager.start_run(
            persona_a=pa,
            persona_b=pb,
            user_id_a="u-a",
            user_id_b="u-b",
            nickname_a="A",
            nickname_b="B",
            turns=3,
        )
        run_id = task.run_id
        await task

        # 최종 DB에서 run의 상태 및 error_reason 검증
        async with real_factory() as db:
            repo = MigrationRepository(db)
            r = await repo.get_run(run_id)
            assert r is not None
            assert r.status == "failed"
            assert r.error_reason == "db_error"

    asyncio.run(with_db(scenario))
