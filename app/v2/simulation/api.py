"""라우터. 검증과 상태코드만 담당하고 로직은 service 로 넘긴다.

  POST /v2/simulation                  : 시뮬레이션 실행 후 결과 반환 (201, v1 처럼 끝날 때까지 기다림).
                                         페르소나 없으면 404, 같은 조합 진행 중이면 409, 실행 실패면 503
  GET  /v2/simulation/jobs/{job_id}    : 작업 상태·단계 (Retry-After: 2). 진행 상황 확인용
  GET  /v2/simulation/{simulation_id}  : 결과 (대본 + 리포트 + 사용한 소재)
  GET  /v2/simulation?user_id=…        : 목록

작업은 새 DB 세션에서 별도 task 로 실행하고 요청은 그 끝을 기다린다.
클라이언트가 연결을 끊어도 작업은 끝까지 돌고, 결과는 GET /jobs/{job_id} 로 확인할 수 있다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal, get_db
from app.features.simulation.schemas import SimulationRequest, SimulationSummary

from .schemas import V2SimulationJobResponse, V2SimulationResponse
from .service import JobInProgress, JobNotFound, PersonaNotFound, SimulationNotFound, SimulationV2Service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v2/simulation", tags=["v2_simulation"])

Runner = Callable[[str], Awaitable[None]]
POLL_INTERVAL_S = 2
_running: set[asyncio.Task[None]] = set()  # 요청이 끊겨도 실행 중인 task 가 GC 되지 않게 잡아 둔다


def get_service(db: AsyncSession = Depends(get_db)) -> SimulationV2Service:
    return SimulationV2Service(db)


# 작업을 새 세션에서 실행한다. 실패는 service.run 이 작업에 기록한다 — 여기서는 예상 못 한 예외만 로그로
async def run_in_background(job_id: str) -> None:
    try:
        async with SessionLocal() as db:
            await SimulationV2Service(db).run(job_id)
    except Exception:
        logger.exception("simulation v2 job %s crashed", job_id)


# 테스트가 가짜로 바꿀 수 있게 의존성으로 둔다
def get_runner() -> Runner:
    return run_in_background


@router.post("", response_model=V2SimulationResponse, status_code=201)
async def start_simulation(
    req: SimulationRequest,
    service: SimulationV2Service = Depends(get_service),
    db: AsyncSession = Depends(get_db),
    runner: Runner = Depends(get_runner),
) -> V2SimulationResponse:
    """가상 매칭을 실행하고, 끝나면 대화와 리포트를 한 응답으로 돌려준다."""
    try:
        job = await service.start(req)
    except PersonaNotFound as e:
        raise HTTPException(
            404, f"{e.who}: 확정된 페르소나가 없어요 ({e.ref.describe()}). 온보딩 → /build → /confirm 을 먼저."
        ) from e
    except JobInProgress as e:
        await db.rollback()
        raise HTTPException(
            409, {"message": "같은 상대와의 시뮬레이션이 이미 진행 중이에요", "job_id": e.job_id}
        ) from e
    await db.commit()
    job_id = job.id  # expire_all 뒤에는 job 속성을 읽으면 동기 조회가 일어나므로 미리 꺼내 둔다

    task = asyncio.create_task(runner(job_id))
    _running.add(task)
    task.add_done_callback(_running.discard)
    await asyncio.shield(task)

    # 실행은 다른 세션이 했다. expire_on_commit=False 라 이 세션의 job 객체는 예전 값이므로 비우고 다시 읽는다
    db.expire_all()
    job = await service.get_job(job_id)
    await db.commit()
    if job.status != "succeeded" or job.simulation_id is None:
        reason = job.error or job.status
        raise HTTPException(
            503,
            {"message": f"simulation failed: {reason}", "reason": reason, "job_id": job.id, "simulation_id": None},
        )
    return await service.get(job.simulation_id)


@router.get("/jobs/{job_id}", response_model=V2SimulationJobResponse)
async def get_job(
    job_id: str,
    response: Response,
    service: SimulationV2Service = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> V2SimulationJobResponse:
    try:
        job = await service.get_job(job_id)
    except JobNotFound as e:
        raise HTTPException(404, "job not found") from e
    # 멈춘 작업은 조회하면서 failed 로 바뀔 수 있어 커밋한다
    await db.commit()
    if job.status in ("pending", "running"):
        response.headers["Retry-After"] = str(POLL_INTERVAL_S)
    return V2SimulationJobResponse(
        job_id=job.id,
        status=job.status,
        stage=job.stage,
        simulation_id=job.simulation_id,
        reason=job.error if job.status == "failed" else None,
        created_at=job.created_at,
        finished_at=job.finished_at,
    )


@router.get("", response_model=list[SimulationSummary])
async def list_simulations(
    user_id: str = Query(min_length=1, max_length=64),
    service: SimulationV2Service = Depends(get_service),
) -> list[SimulationSummary]:
    """내가 요청자든 상대든 다 나온다. 최신순."""
    return await service.list_for(user_id)


@router.get("/{simulation_id}", response_model=V2SimulationResponse)
async def get_simulation(
    simulation_id: str, service: SimulationV2Service = Depends(get_service)
) -> V2SimulationResponse:
    try:
        return await service.get(simulation_id)
    except SimulationNotFound as e:
        raise HTTPException(404, "simulation not found") from e
