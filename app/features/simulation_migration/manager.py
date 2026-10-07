"""시뮬레이션 마이그레이션 RunManager.

그래프 실행의 수명, 세션 팩토리, 프로세스 슬롯, attempt fencing을 관리한다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.features.persona.schemas import PersonaResponse
from app.features.simulation_migration.llm import LLMError
from app.features.simulation_migration.repository import MigrationRepository

logger = logging.getLogger(__name__)


class SlotUnavailable(Exception):
    """프로세스 동시 실행 슬롯 부족 예외."""


class ConflictError(Exception):
    """상태 전이 또는 동시성 충돌 예외."""


class RunNotFound(Exception):
    """시뮬레이션 실행 행 미존재 예외."""


class RegisteredRun(NamedTuple):
    task: asyncio.Task
    attempt: int


class _LLMWrapper:
    """화자 도구 호출 직전 before_request를 실행하여 소유권을 검증하는 래퍼."""

    def __init__(self, inner: Any, before_req: Callable[[], Awaitable[bool]]) -> None:
        self.inner = inner
        self.before_req = before_req

    async def __call__(self, messages: list[dict[str, str]]) -> str:
        ok = await self.before_req()
        if not ok:
            raise LLMError("소유권을 상실했습니다 (fencing)", reason="lost_ownership")

        if callable(self.inner):
            res = self.inner(messages)
            if asyncio.iscoroutine(res) or asyncio.isfuture(res):
                return await res
            return res

        from app.features.simulation_migration.llm import complete_text

        return await complete_text(messages, client=self.inner, before_request=self.before_req)

    async def complete_text(self, messages: list[dict[str, str]]) -> str:
        ok = await self.before_req()
        if not ok:
            raise LLMError("소유권을 상실했습니다 (fencing)", reason="lost_ownership")

        if hasattr(self.inner, "complete_text"):
            res = self.inner.complete_text(messages)
            if asyncio.iscoroutine(res) or asyncio.isfuture(res):
                return await res
            return res

        from app.features.simulation_migration.llm import complete_text

        return await complete_text(messages, client=self.inner, before_request=self.before_req)


class RunManager:
    """시뮬레이션 마이그레이션 실행 생명주기 및 펜싱 관리자."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        max_runs: int | None = None,
        stale_s: float | None = None,
        report_heartbeat_interval_s: float = 30.0,
        build_graph: Callable[..., Any] | None = None,
    ) -> None:
        self.session_factory = session_factory
        settings = get_settings()
        self.max_runs = max_runs if max_runs is not None else settings.simulation_migration_max_runs
        self.stale_s = stale_s if stale_s is not None else settings.simulation_migration_stale_s
        self.report_heartbeat_interval_s = report_heartbeat_interval_s
        self.build_graph = build_graph or self._default_build_graph

        self._active_slots: int = 0
        self._tasks: set[asyncio.Task] = set()
        self._registered_runs: dict[tuple[str, int], RegisteredRun] = {}

    @staticmethod
    def _default_build_graph(*args: Any, **kwargs: Any) -> Any:
        import app.features.simulation_migration.graph as graph_mod

        factory = getattr(graph_mod, "build_graph", None) or graph_mod.create_simulation_graph
        return factory(*args, **kwargs)

    def acquire_run_slot(self) -> bool:
        """원자적으로 실행 슬롯을 획득한다."""
        if self._active_slots < self.max_runs:
            self._active_slots += 1
            return True
        return False

    def release_run_slot(self) -> None:
        """실행 슬롯을 반환한다."""
        if self._active_slots > 0:
            self._active_slots -= 1

    async def start_run(
        self,
        *,
        persona_a: PersonaResponse,
        persona_b: PersonaResponse,
        user_id_a: str,
        user_id_b: str,
        nickname_a: str,
        nickname_b: str,
        turns: int = 10,
        run_id: str | None = None,
        llm: Any = None,
        write_report: Callable | None = None,
        slot_acquired: bool = False,
    ) -> asyncio.Task:
        """새 시뮬레이션 실행 행을 생성하고 비동기 task를 시작한다.

        슬롯이 미리 획득되지 않았으면 원자적으로 얻으며, insert_run 실패 시 슬롯을 반환한다.
        """
        locally_acquired = False
        if not slot_acquired:
            if not self.acquire_run_slot():
                raise SlotUnavailable(f"최대 동시 실행 수({self.max_runs})에 도달했습니다.")
            locally_acquired = True

        actual_run_id = run_id
        try:
            async with self.session_factory() as db:
                repo = MigrationRepository(db)
                run = await repo.insert_run(
                    id=actual_run_id,
                    persona_a_id=persona_a.persona_id,
                    persona_b_id=persona_b.persona_id,
                    user_id_a=user_id_a,
                    user_id_b=user_id_b,
                    nickname_a=nickname_a,
                    nickname_b=nickname_b,
                    turns=turns,
                )
                actual_run_id = run.id
                await db.commit()
        except Exception:
            if locally_acquired:
                self.release_run_slot()
            raise

        task = asyncio.create_task(
            self._execute_run(
                run_id=actual_run_id,
                attempt=1,
                persona_a=persona_a,
                persona_b=persona_b,
                nickname_a=nickname_a,
                nickname_b=nickname_b,
                turns=turns,
                llm=llm,
                write_report=write_report,
            )
        )
        task.run_id = actual_run_id
        task.attempt = 1
        self._register_task(actual_run_id, 1, task)
        return task

    async def resume(
        self,
        run_id: str,
        *,
        persona_a: PersonaResponse | None = None,
        persona_b: PersonaResponse | None = None,
        llm: Any = None,
        write_report: Callable | None = None,
        slot_acquired: bool = False,
    ) -> asyncio.Task:
        """중단되었거나 실패한 시뮬레이션을 재개한다.

        슬롯을 요구하며, conditional_update로 attempt를 1 증가시키고 running으로 전이한다.
        """
        locally_acquired = False
        if not slot_acquired:
            if not self.acquire_run_slot():
                raise SlotUnavailable(f"최대 동시 실행 수({self.max_runs})에 도달했습니다.")
            locally_acquired = True

        try:
            async with self.session_factory() as db:
                repo = MigrationRepository(db)
                run = await repo.get_run(run_id)
                if run is None:
                    raise RunNotFound(f"Run {run_id} not found")
                if run.status not in ("failed", "aborted"):
                    raise ConflictError(f"Run {run_id} is in status '{run.status}', cannot resume")

                p_a = persona_a
                p_b = persona_b
                if p_a is None or p_b is None:
                    from app.features.persona.lookup import load_persona
                    from app.features.persona.schemas import PersonaRef

                    if p_a is None:
                        loaded_a = await load_persona(db, PersonaRef(persona_id=run.persona_a_id))
                        if loaded_a is None:
                            raise ConflictError("persona_unavailable")
                        p_a = loaded_a.response
                    if p_b is None:
                        loaded_b = await load_persona(db, PersonaRef(persona_id=run.persona_b_id))
                        if loaded_b is None:
                            raise ConflictError("persona_unavailable")
                        p_b = loaded_b.response

                next_attempt = run.attempt + 1
                now = datetime.now(UTC)
                updated = await repo.conditional_update(
                    run_id,
                    run.attempt,
                    ["failed", "aborted"],
                    status="running",
                    attempt=next_attempt,
                    error_reason=None,
                    heartbeat_at=now,
                )
                if not updated:
                    raise ConflictError("동시 재개 또는 소유권 상실로 인해 conditional_update에 실패했습니다.")
                await db.commit()

                nickname_a = run.nickname_a
                nickname_b = run.nickname_b
                turns = run.turns
        except Exception:
            if locally_acquired:
                self.release_run_slot()
            raise

        task = asyncio.create_task(
            self._execute_run(
                run_id=run_id,
                attempt=next_attempt,
                persona_a=p_a,
                persona_b=p_b,
                nickname_a=nickname_a,
                nickname_b=nickname_b,
                turns=turns,
                llm=llm,
                write_report=write_report,
            )
        )
        task.run_id = run_id
        task.attempt = next_attempt
        self._register_task(run_id, next_attempt, task)
        return task

    async def abort_if_stale(self, run_id: str, attempt: int | None = None) -> bool:
        """heartbeat가 stale_s보다 오래되었고 running/reporting인 행을 aborted로 전이한다."""
        stale_before = datetime.now(UTC) - timedelta(seconds=self.stale_s)
        async with self.session_factory() as db:
            repo = MigrationRepository(db)
            aborted = await repo.abort_stale(run_id, stale_before=stale_before, attempt=attempt)
            if aborted:
                await db.commit()
            return aborted

    async def shutdown(self) -> None:
        """자신이 관리하는 실행 task들을 취소하고 DB 상태를 aborted로 갱신한다."""
        runs = list(self._registered_runs.items())
        tasks = [info.task for _, info in runs]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        async with self.session_factory() as db:
            repo = MigrationRepository(db)
            for (r_id, attempt_val), _ in runs:
                await repo.conditional_update(
                    r_id,
                    attempt_val,
                    ["running", "reporting"],
                    status="aborted",
                    error_reason="aborted",
                )
            await db.commit()

    def _register_task(self, run_id: str, attempt: int, task: asyncio.Task) -> None:
        key = (run_id, attempt)
        self._tasks.add(task)
        self._registered_runs[key] = RegisteredRun(task=task, attempt=attempt)

        def _on_done(t: asyncio.Task) -> None:
            self._tasks.discard(t)
            self._registered_runs.pop(key, None)
            self.release_run_slot()

            if t.cancelled():
                return

            exc = t.exception()
            if exc is not None:
                logger.error(
                    "Task for run %s (attempt %d) failed with exception: %s", run_id, attempt, exc, exc_info=exc
                )
                mark_task = asyncio.create_task(self._mark_task_error(run_id, attempt))
                self._tasks.add(mark_task)
                mark_task.add_done_callback(self._tasks.discard)

        task.add_done_callback(_on_done)

    async def _mark_task_error(self, run_id: str, attempt: int) -> None:
        try:
            async with self.session_factory() as db:
                repo = MigrationRepository(db)
                await repo.conditional_update(
                    run_id,
                    attempt,
                    ["running", "reporting"],
                    status="failed",
                    error_reason="task_error",
                )
                await db.commit()
        except Exception as e:
            logger.error("Failed to mark run %s as task_error: %s", run_id, e)

    async def _execute_run(
        self,
        *,
        run_id: str,
        attempt: int,
        persona_a: PersonaResponse,
        persona_b: PersonaResponse,
        nickname_a: str,
        nickname_b: str,
        turns: int,
        llm: Any = None,
        write_report: Callable | None = None,
    ) -> None:
        """개별 시뮬레이션의 비동기 실행 루프."""
        try:
            # 1. 기존 발화 목록 불러오기
            async with self.session_factory() as db:
                repo = MigrationRepository(db)
                utterances = await repo.list_utterances(run_id)
                transcript = [(u.speaker, u.text) for u in utterances]

            # 2. HTTP 직전 heartbeat 갱신 콜백 정의
            async def before_request() -> bool:
                now = datetime.now(UTC)
                try:
                    async with self.session_factory() as db:
                        r = MigrationRepository(db)
                        ok = await r.conditional_update(
                            run_id,
                            attempt,
                            ["running"],
                            heartbeat_at=now,
                        )
                        if ok:
                            await db.commit()
                        return ok
                except Exception as e:
                    raise LLMError(
                        f"Database error during before_request: {e}", reason="db_error", retryable=False
                    ) from e

            # 3. 리포트 작성 핸들러 정의
            async def report_handler(state: Any) -> Any:
                current_transcript = state.transcript if hasattr(state, "transcript") else state["transcript"]
                if len(current_transcript) < turns * 2:
                    # 대사가 가득 차기 전에는 reporting으로 가지 않는다
                    return None

                async with self.session_factory() as db:
                    r = MigrationRepository(db)
                    try:
                        ok = await r.conditional_update(
                            run_id,
                            attempt,
                            ["running"],
                            status="reporting",
                        )
                        if not ok:
                            return None
                        await db.commit()
                    except Exception:
                        await db.rollback()
                        try:
                            await r.conditional_update(
                                run_id,
                                attempt,
                                ["running"],
                                status="failed",
                                error_reason="db_error",
                            )
                            await db.commit()
                        except Exception:
                            pass
                        raise

                async def run_report() -> Any:
                    if write_report is not None:
                        if asyncio.iscoroutinefunction(write_report):
                            return await write_report(state)
                        return write_report(state)
                    from app.features.simulation_migration.report_tool import write_matching_report

                    return await write_matching_report(state)

                write_task = asyncio.create_task(run_report())
                self._tasks.add(write_task)
                write_task.add_done_callback(self._tasks.discard)

                lost_during_reporting = False

                while not write_task.done():
                    try:
                        await asyncio.wait_for(asyncio.shield(write_task), timeout=self.report_heartbeat_interval_s)
                        break
                    except TimeoutError:
                        pass
                    if write_task.done():
                        break

                    hb_now = datetime.now(UTC)
                    try:
                        async with self.session_factory() as hb_db:
                            hb_repo = MigrationRepository(hb_db)
                            hb_ok = await hb_repo.conditional_update(
                                run_id,
                                attempt,
                                ["reporting"],
                                heartbeat_at=hb_now,
                            )
                            if not hb_ok:
                                write_task.cancel()
                                lost_during_reporting = True
                                break
                            await hb_db.commit()
                    except Exception:
                        write_task.cancel()
                        try:
                            async with self.session_factory() as fail_db:
                                fail_repo = MigrationRepository(fail_db)
                                await fail_repo.conditional_update(
                                    run_id,
                                    attempt,
                                    ["reporting"],
                                    status="failed",
                                    error_reason="db_error",
                                )
                                await fail_db.commit()
                        except Exception:
                            pass
                        raise

                if lost_during_reporting:
                    return None

                try:
                    rep_data = await write_task
                except asyncio.CancelledError:
                    return None

                narrative_source = "llm"
                validation_data = None
                if isinstance(rep_data, dict):
                    if rep_data.get("narrative_source"):
                        narrative_source = str(rep_data["narrative_source"])
                    elif rep_data.get("source"):
                        narrative_source = str(rep_data["source"])
                    if "validation" in rep_data:
                        validation_data = rep_data["validation"]

                # reporting -> done
                async with self.session_factory() as db:
                    r = MigrationRepository(db)
                    try:
                        update_fields: dict[str, Any] = {
                            "status": "done",
                            "report": rep_data if isinstance(rep_data, dict) else {"content": str(rep_data)},
                            "narrative_source": narrative_source,
                        }
                        if validation_data is not None:
                            update_fields["validation"] = validation_data

                        ok = await r.conditional_update(
                            run_id,
                            attempt,
                            ["reporting"],
                            **update_fields,
                        )
                        if ok:
                            await db.commit()
                    except Exception:
                        await db.rollback()
                        try:
                            await r.conditional_update(
                                run_id,
                                attempt,
                                ["reporting"],
                                status="failed",
                                error_reason="db_error",
                            )
                            await db.commit()
                        except Exception:
                            pass
                        raise

                return rep_data

            # 4. 그래프 생성 및 실행
            llm_wrapped = _LLMWrapper(llm, before_request)
            graph = self.build_graph(llm=llm_wrapped, write_report=report_handler)

            initial_state = {
                "run_id": run_id,
                "attempt": attempt,
                "persona_a": persona_a,
                "persona_b": persona_b,
                "nickname_a": nickname_a,
                "nickname_b": nickname_b,
                "turns": turns,
                "transcript": transcript,
            }

            async for event in graph.astream(initial_state):
                for node_name, node_output in event.items():
                    if node_name in ("speak_a", "speak_b"):
                        err = (
                            node_output.get("error")
                            if isinstance(node_output, dict)
                            else getattr(node_output, "error", None)
                        )
                        if err:
                            if err == "lost_ownership":
                                return

                            async with self.session_factory() as db:
                                r = MigrationRepository(db)
                                await r.conditional_update(
                                    run_id,
                                    attempt,
                                    ["running", "reporting"],
                                    status="failed",
                                    error_reason=err,
                                )
                                await db.commit()
                            return

                        val_obj = (
                            node_output.get("validation")
                            if isinstance(node_output, dict)
                            else getattr(node_output, "validation", None)
                        )
                        val_dict = (
                            val_obj.model_dump(mode="json")
                            if hasattr(val_obj, "model_dump")
                            else (val_obj if isinstance(val_obj, dict) else None)
                        )

                        new_transcript = (
                            node_output.get("transcript")
                            if isinstance(node_output, dict)
                            else getattr(node_output, "transcript", None)
                        )
                        if new_transcript and len(new_transcript) > len(transcript):
                            new_line = new_transcript[-1]
                            idx = len(new_transcript) - 1
                            spk = new_line[0]
                            txt = new_line[1]

                            async with self.session_factory() as db:
                                r = MigrationRepository(db)
                                res = await r.insert_utterance_if_owner(
                                    run_id=run_id,
                                    attempt=attempt,
                                    index=idx,
                                    speaker=spk,
                                    text=txt,
                                    validation=val_dict,
                                )
                                if res == "lost":
                                    return
                                elif res == "inserted":
                                    await db.commit()
                                    transcript = list(new_transcript)

                                    if val_obj is not None:
                                        from app.core.guardrail import effective_mode
                                        from app.core.guardrail_trace import record_guardrail

                                        g_mode = effective_mode(None)
                                        if g_mode != "off":
                                            try:
                                                async with self.session_factory() as trace_db:
                                                    await record_guardrail(
                                                        trace_db,
                                                        feature="simulation",
                                                        operation="migration_line",
                                                        session_id=run_id,
                                                        user_id=None,
                                                        mode=g_mode,
                                                        result=val_obj,
                                                        initial_text=txt,
                                                    )
                                                    await trace_db.commit()
                                            except Exception as trace_err:
                                                logger.warning("Failed to record guardrail trace: %s", trace_err)
                                elif res == "exists":
                                    transcript = list(new_transcript)

                    elif node_name == "report":
                        pass

        except asyncio.CancelledError:
            raise
        except Exception:
            try:
                async with self.session_factory() as db:
                    r = MigrationRepository(db)
                    await r.conditional_update(
                        run_id,
                        attempt,
                        ["running", "reporting"],
                        status="failed",
                        error_reason="task_error",
                    )
                    await db.commit()
            except Exception:
                pass
            raise
