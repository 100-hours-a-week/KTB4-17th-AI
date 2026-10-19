"""라우터. 검증과 상태코드만 담당하고 로직은 service 로 넘긴다.

  POST /v2/simulation                  : 시뮬레이션 작업 생성 (202, 비동기). 페르소나 없으면 404, 같은 조합 진행 중이면 409
  GET  /v2/simulation/jobs/{job_id}    : 작업 상태·단계 (Retry-After: 2)
  GET  /v2/simulation/{simulation_id}  : 결과 (대본 + 리포트 + 사용한 소재)
  GET  /v2/simulation?user_id=…        : 목록

작업은 응답을 보낸 뒤 BackgroundTasks 로 실행한다. 요청 DB 세션은 응답과 함께 닫히므로 새 세션을 연다.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal, get_db
from app.core.security import require_internal_key
from app.features.simulation.schemas import SimulationRequest, SimulationSummary

from .schemas import V2SimulationJobCreated, V2SimulationJobResponse, V2SimulationResponse
from .service import JobInProgress, JobNotFound, PersonaNotFound, SimulationNotFound, SimulationV2Service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v2/simulation", tags=["v2_simulation"], dependencies=[Depends(require_internal_key)])

Runner = Callable[[str], Awaitable[None]]
POLL_INTERVAL_S = 2


def get_service(db: AsyncSession = Depends(get_db)) -> SimulationV2Service:
    return SimulationV2Service(db)


# 응답 뒤 작업을 실행한다. 실패는 service.run 이 작업에 기록한다 — 여기서는 예상 못 한 예외만 로그로
async def run_in_background(job_id: str) -> None:
    try:
        async with SessionLocal() as db:
            await SimulationV2Service(db).run(job_id)
    except Exception:
        logger.exception("simulation v2 job %s crashed", job_id)


# 테스트가 가짜로 바꿀 수 있게 의존성으로 둔다
def get_runner() -> Runner:
    return run_in_background


@router.post("", response_model=V2SimulationJobCreated, status_code=202)
async def start_simulation(
    req: SimulationRequest,
    background_tasks: BackgroundTasks,
    service: SimulationV2Service = Depends(get_service),
    db: AsyncSession = Depends(get_db),
    runner: Runner = Depends(get_runner),
) -> V2SimulationJobCreated:
    """가상 매칭 작업을 만든다. 결과는 GET /jobs/{job_id} 로 폴링한 뒤 simulation_id 로 조회한다."""
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
    background_tasks.add_task(runner, job.id)
    return V2SimulationJobCreated(job_id=job.id, status="pending")


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
