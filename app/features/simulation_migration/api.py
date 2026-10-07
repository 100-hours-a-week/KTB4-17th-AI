from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.db import SessionLocal, get_db
from app.features.persona.lookup import load_persona
from app.features.persona.schemas import PersonaRef
from app.features.simulation.schemas import MatchingReport, SimulationResponse, Turn
from app.features.simulation_migration.manager import ConflictError, RunManager, SlotUnavailable
from app.features.simulation_migration.models import SimulationMigrationRun
from app.features.simulation_migration.repository import MigrationRepository, pair_key
from app.features.simulation_migration.schemas import (
    MigrationAccepted,
    MigrationDoneEvent,
    MigrationErrorEvent,
    MigrationListItem,
    MigrationReportEvent,
    MigrationReportView,
    MigrationRunEvent,
    MigrationRunView,
    MigrationStartRequest,
    MigrationUtteranceEvent,
)
from app.features.simulation_migration.service import (
    PersonaNotFound,
    SimulationMigrationService,
    SimulationNotFound,
)


# SQLite 등에서 naive datetime으로 로드되는 경우 UTC tzinfo 보정
@event.listens_for(SimulationMigrationRun, "load")
def _ensure_timezone(target: SimulationMigrationRun, context: Any) -> None:
    for attr in ("heartbeat_at", "created_at", "updated_at"):
        val = getattr(target, attr, None)
        if val is not None and isinstance(val, datetime) and val.tzinfo is None:
            setattr(target, attr, val.replace(tzinfo=UTC))


router = APIRouter(prefix="/v1/simulation", tags=["simulation"])

_global_run_manager: RunManager | None = None
_active_event_streams: int = 0
_stream_lock = threading.Lock()


def _handle_integrity_error(e: IntegrityError, manager: RunManager | None = None) -> None:
    """partial unique 제약 위반을 적절한 HTTP 상태 코드로 변환한다."""
    if manager is not None:
        manager.release_run_slot()
    orig_msg = str(getattr(e, "orig", "") or "").lower()
    if not orig_msg:
        orig_msg = str(e).lower()

    if "uq_simulation_migration_runs_pair_active" in orig_msg or "pair_key" in orig_msg:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="동일 페르소나 쌍의 활성 시뮬레이션이 이미 존재합니다.",
        ) from e
    if "uq_simulation_migration_runs_user_a_active" in orig_msg or "user_id_a" in orig_msg:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="동일 사용자의 활성 시뮬레이션이 이미 존재합니다.",
        ) from e
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="무결성 제약 조건 위반입니다.") from e


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """세션 팩토리를 반환한다 (테스트에서 override 가능)."""
    return SessionLocal


def get_run_manager(
    session_factory: Any = Depends(get_session_factory),
) -> RunManager:
    """RunManager 싱글톤 인스턴스를 반환한다.

    FastAPI 컨텍스트 밖에서 호출되어 Depends 객체로 남아있으면 SessionLocal을 사용한다.
    """
    global _global_run_manager
    if _global_run_manager is None:
        factory = session_factory if callable(session_factory) else SessionLocal
        _global_run_manager = RunManager(session_factory=factory)
    return _global_run_manager


def get_service(
    db: AsyncSession = Depends(get_db),
    manager: RunManager = Depends(get_run_manager),
) -> SimulationMigrationService:
    """시뮬레이션 마이그레이션 서비스를 생성한다."""
    return SimulationMigrationService(db, manager)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=SimulationResponse,
    summary="시뮬레이션 시작",
)
async def start_simulation(
    req: MigrationStartRequest,
    db: AsyncSession = Depends(get_db),
    manager: RunManager = Depends(get_run_manager),
) -> SimulationResponse:
    """시뮬레이션이 끝날 때까지 기다리고, 대화와 리포트를 한 응답으로 돌려준다."""
    settings = get_settings()
    now = datetime.now(UTC)
    stale_threshold = now - timedelta(seconds=settings.simulation_migration_stale_s)
    repo = MigrationRepository(db)

    # 1. user_id_a의 진행 중 여부 검사 (fresh면 429)
    stmt = select(SimulationMigrationRun).where(
        SimulationMigrationRun.user_id_a == req.me_user_id,
        SimulationMigrationRun.status.in_(["running", "reporting"]),
    )
    user_active = (await db.execute(stmt)).scalars().first()
    if user_active is not None:
        if user_active.heartbeat_at < stale_threshold:
            aborted = await repo.abort_stale(user_active.id, stale_before=stale_threshold)
            if aborted:
                await db.commit()
        else:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="해당 사용자의 진행 중인 시뮬레이션이 이미 존재합니다.",
            )

    # 2. 페르소나 로드 및 pair_key 검사
    loaded_a = await load_persona(db, PersonaRef(user_id=req.me_user_id))
    if loaded_a is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="요청자 페르소나를 찾을 수 없습니다.")

    loaded_b = await load_persona(db, PersonaRef(user_id=req.partner_user_id))
    if loaded_b is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="상대방 페르소나를 찾을 수 없습니다.")

    p_key = pair_key(loaded_a.response.persona_id, loaded_b.response.persona_id)
    active_pair = await repo.find_active_by_pair(p_key)
    if active_pair is not None:
        if active_pair.heartbeat_at < stale_threshold:
            aborted = await repo.abort_stale(active_pair.id, stale_before=stale_threshold)
            if aborted:
                await db.commit()
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"해당 페르소나 쌍의 시뮬레이션이 이미 진행 중입니다 ({active_pair.status}).",
            )

    # 3. 프로세스 슬롯 획득
    if not manager.acquire_run_slot():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"최대 동시 실행 수({manager.max_runs})에 도달했습니다.",
        )

    # 4. RunManager를 통해 시작 (insert 실패 시 슬롯 반환)
    user_id_b = req.partner_user_id
    try:
        task = await manager.start_run(
            persona_a=loaded_a.response,
            persona_b=loaded_b.response,
            user_id_a=req.me_user_id,
            user_id_b=user_id_b,
            nickname_a=loaded_a.nickname,
            nickname_b=loaded_b.nickname,
            turns=req.turns,
            slot_acquired=True,
        )
        run_id = task.run_id
        try:
            await task
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "message": f"simulation failed: {e}",
                    "reason": "task_error",
                    "simulation_id": run_id,
                },
            ) from e
        return await _completed_response(
            manager.session_factory,
            run_id,
            me=loaded_a.brief,
            partner=loaded_b.brief,
        )
    except PersonaNotFound as e:
        manager.release_run_slot()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except SlotUnavailable as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e)) from e
    except IntegrityError as e:
        _handle_integrity_error(e, manager)
    except Exception:
        manager.release_run_slot()
        raise


@router.post(
    "/{id}/resume",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=MigrationAccepted,
    summary="시뮬레이션 재개",
)
async def resume_simulation(
    id: str = Path(..., description="시뮬레이션 ID"),
    me_user_id: str = Query(..., description="요청자 user_id"),
    service: SimulationMigrationService = Depends(get_service),
    manager: RunManager = Depends(get_run_manager),
) -> MigrationAccepted:
    """중단되었거나 실패한 시뮬레이션을 재개한다."""
    try:
        sim_id = await service.resume_simulation(id, me_user_id=me_user_id)
        return MigrationAccepted(simulation_id=sim_id)
    except SimulationNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except SlotUnavailable as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e)) from e
    except ConflictError as e:
        if str(e) == "persona_unavailable":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="persona_unavailable") from e
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except IntegrityError as e:
        _handle_integrity_error(e)


@router.get(
    "/{id}/events",
    summary="시뮬레이션 스트리밍",
    response_class=StreamingResponse,
)
async def get_events(
    id: str = Path(..., description="시뮬레이션 ID"),
    request: Request = None,  # type: ignore[assignment]
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
) -> StreamingResponse:
    """이미 시작된 실행의 발화를 SSE로 전달한다. 대화와 리포트를 한 번에 받으려면 POST 응답을 쓴다."""
    global _active_event_streams
    settings = get_settings()

    # await 전에 락으로 검사와 증가를 같이 수행 (m5)
    with _stream_lock:
        if _active_event_streams >= settings.simulation_migration_max_event_streams:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="동시 이벤트 스트림 연결 상한에 도달했습니다.",
            )
        _active_event_streams += 1

    try:
        # 행 존재 여부 사전 확인
        async with session_factory() as db:
            repo = MigrationRepository(db)
            initial_run = await repo.get_run(id)
            if initial_run is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="시뮬레이션을 찾을 수 없습니다.")
    except Exception:
        with _stream_lock:
            _active_event_streams -= 1
        raise

    async def event_generator() -> AsyncIterator[str]:
        global _active_event_streams
        try:
            last_sent_index = -1
            if last_event_id is not None and last_event_id.isdigit():
                last_sent_index = int(last_event_id)

            # 1. 최초 run 이벤트 전송
            run_evt = MigrationRunEvent(
                simulation_id=initial_run.id,
                turns=initial_run.turns,
                status=initial_run.status,
            )
            yield f"event: run\ndata: {run_evt.model_dump_json()}\n\n"

            last_ping_time = time.monotonic()

            while True:
                if request is not None and await request.is_disconnected():
                    break

                events_to_yield: list[str] = []
                should_break = False

                async with session_factory() as db:
                    repo = MigrationRepository(db)
                    current_run = await repo.get_run(id)
                    if current_run is None:
                        should_break = True
                    else:
                        # 대사 전송
                        utterances = await repo.list_utterances(id)
                        for u in utterances:
                            if u.index > last_sent_index:
                                nickname = current_run.nickname_a if u.speaker == "a" else current_run.nickname_b
                                utt_evt = MigrationUtteranceEvent(
                                    id=u.index,
                                    index=u.index,
                                    speaker=u.speaker,  # type: ignore[arg-type]
                                    nickname=nickname,
                                    text=u.text,
                                )
                                events_to_yield.append(
                                    f"event: utterance\nid: {u.index}\ndata: {utt_evt.model_dump_json()}\n\n"
                                )
                                last_sent_index = u.index
                                last_ping_time = time.monotonic()

                        # 상태 확인 (stale 판정은 abort_stale 사용, m4, n1)
                        now = datetime.now(UTC)
                        stale_thresh = now - timedelta(seconds=settings.simulation_migration_stale_s)
                        skip_status_check = False
                        if current_run.status in ("running", "reporting") and current_run.heartbeat_at < stale_thresh:
                            aborted = await repo.abort_stale(
                                id,
                                stale_before=stale_thresh,
                                attempt=current_run.attempt,
                            )
                            if aborted:
                                await db.commit()
                                err_evt = MigrationErrorEvent(
                                    reason="aborted",
                                    message="실행이 중단되었습니다 (stale)",
                                )
                                events_to_yield.append(f"event: error\ndata: {err_evt.model_dump_json()}\n\n")
                                should_break = True
                            else:
                                skip_status_check = True

                        if not should_break and not skip_status_check:
                            if current_run.status == "done":
                                if current_run.report is not None:
                                    rep_model = MatchingReport.model_validate(current_run.report)
                                    rep_evt = MigrationReportEvent(report=rep_model)
                                    events_to_yield.append(f"event: report\ndata: {rep_evt.model_dump_json()}\n\n")
                                done_evt = MigrationDoneEvent(simulation_id=current_run.id, status="done")
                                events_to_yield.append(f"event: done\ndata: {done_evt.model_dump_json()}\n\n")
                                should_break = True
                            elif current_run.status in ("failed", "aborted"):
                                err_evt = MigrationErrorEvent(
                                    reason=current_run.error_reason or current_run.status,
                                    message=current_run.error_reason or "시뮬레이션 실행 실패",
                                )
                                events_to_yield.append(f"event: error\ndata: {err_evt.model_dump_json()}\n\n")
                                should_break = True

                # 세션을 닫은 후 yield (m9)
                for evt_str in events_to_yield:
                    yield evt_str

                if should_break:
                    break

                # 15초 무이벤트 ping 전송
                if time.monotonic() - last_ping_time >= 15.0:
                    yield ": ping\n\n"
                    last_ping_time = time.monotonic()

                await asyncio.sleep(1.0)
        finally:
            with _stream_lock:
                _active_event_streams -= 1

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/{id}",
    response_model=MigrationRunView,
    summary="단건 시뮬레이션 상태 조회",
)
async def get_simulation(
    id: str = Path(..., description="시뮬레이션 ID"),
    service: SimulationMigrationService = Depends(get_service),
) -> MigrationRunView:
    """단건 시뮬레이션 상태를 조회한다 (stale이면 표시 상태만 aborted로 계산)."""
    try:
        return await service.get_simulation(id)
    except SimulationNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.get(
    "/{id}/report",
    response_model=MigrationReportView,
    summary="시뮬레이션 완료 리포트 조회",
)
async def get_simulation_report(
    id: str = Path(..., description="시뮬레이션 ID"),
    db: AsyncSession = Depends(get_db),
) -> MigrationReportView:
    """완료된 시뮬레이션의 매칭 리포트를 조회한다 (완료되지 않았으면 409)."""
    repo = MigrationRepository(db)
    run = await repo.get_run(id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="시뮬레이션을 찾을 수 없습니다.")

    if run.status != "done" or run.report is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="not_ready")

    rep_model = MatchingReport.model_validate(run.report)
    return MigrationReportView(
        simulation_id=run.id,
        report=rep_model,
        narrative_source=run.narrative_source,  # type: ignore[arg-type]
    )


async def _completed_response(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: str,
    *,
    me: Any,
    partner: Any,
) -> SimulationResponse:
    """끝난 실행의 대화와 리포트를 기존 시뮬레이션과 같은 응답 모양으로 만든다."""
    async with session_factory() as db:
        repo = MigrationRepository(db)
        run = await repo.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="시뮬레이션을 찾을 수 없습니다.")
        if run.status != "done" or run.report is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "message": "simulation failed",
                    "reason": run.error_reason or run.status,
                    "simulation_id": run.id,
                },
            )
        try:
            report = MatchingReport.model_validate(run.report)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "message": f"simulation failed: {e}",
                    "reason": "invalid_report",
                    "simulation_id": run.id,
                },
            ) from e
        utterances = await repo.list_utterances(run_id)
    return SimulationResponse(
        simulation_id=run.id,
        validationResult=report.validationResult,
        me=me,
        partner=partner,
        turns=run.turns,
        transcript=[
            Turn(index=item.index, speaker=item.speaker, text=item.text)  # type: ignore[arg-type]
            for item in utterances
        ],
        report=report,
        created_at=run.created_at,
    )


@router.get(
    "",
    response_model=list[MigrationListItem],
    summary="시뮬레이션 목록 조회",
)
async def list_simulations(
    user_id: str | None = Query(default=None, description="사용자 ID"),
    persona_id: str | None = Query(default=None, description="페르소나 ID"),
    limit: int = Query(default=20, ge=1, le=100, description="조회 개수"),
    service: SimulationMigrationService = Depends(get_service),
) -> list[MigrationListItem]:
    """user_id 또는 persona_id 중 하나를 기준으로 시뮬레이션 목록을 반환한다."""
    if (user_id is None and persona_id is None) or (user_id is not None and persona_id is not None):
        raise HTTPException(
            status_code=422,
            detail="user_id 또는 persona_id 중 정확히 하나만 지정해야 합니다.",
        )

    if user_id is not None:
        return await service.list_for_user(user_id, limit=limit)
    return await service.list_for_persona(persona_id, limit=limit)
