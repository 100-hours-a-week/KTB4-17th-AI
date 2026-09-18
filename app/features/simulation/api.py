"""라우터. 검증과 상태코드만 담당하고 로직은 service/report 로 넘긴다.

POST /v1/simulation                 : 내 페르소나 × 상대 페르소나 → 10턴 대본 + 매칭 리포트 (LLM 1회). 저장됨
GET  /v1/simulation/{id}            : 저장된 시뮬레이션 (대본 + 리포트)
GET  /v1/simulation/{id}/report     : 리포트만
GET  /v1/simulation?user_id=…       : 내 시뮬레이션 목록
POST /v1/simulation/report/preview  : 페르소나 둘 + 대화록을 직접 넣어 리포트만. 프론트·플레이그라운드용
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.features.persona.schemas import PersonaRef

from .agents import ReportAgent, SimulationFailed
from .report import build_report
from .schemas import MatchingReport, ReportInput, SimulationRequest, SimulationResponse, SimulationSummary
from .service import PersonaNotFound, SimulationNotFound, SimulationService

router = APIRouter(prefix="/v1/simulation", tags=["simulation"])


def get_service(db: AsyncSession = Depends(get_db)) -> SimulationService:
    return SimulationService(db)


@router.post("", response_model=SimulationResponse, status_code=201)
async def run_simulation(
    req: SimulationRequest,
    service: SimulationService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> SimulationResponse:
    """가상 매칭. 두 페르소나가 `turns` 왕복(기본 10) 대화한 대본과 매칭 리포트를 한 번에 돌려준다."""
    try:
        result = await service.run(req)
    except PersonaNotFound as e:
        raise HTTPException(
            404, f"{e.who}: 저장된 페르소나가 없어요 ({e.ref.describe()}). 온보딩 후 /build 를 먼저."
        ) from e
    except SimulationFailed as e:
        await db.rollback()
        raise HTTPException(503, f"simulation failed: {e}") from e
    await db.commit()
    return result


@router.get("", response_model=list[SimulationSummary])
async def list_simulations(
    user_id: str | None = Query(default=None),
    persona_id: str | None = Query(default=None),
    service: SimulationService = Depends(get_service),
) -> list[SimulationSummary]:
    """user_id 또는 persona_id 중 하나로. 내가 a 든 b 든 다 나온다."""
    try:
        ref = PersonaRef(user_id=user_id, persona_id=persona_id)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    try:
        return await service.list_for(ref)
    except PersonaNotFound as e:
        raise HTTPException(404, f"저장된 페르소나가 없어요 ({e.ref.describe()})") from e


@router.post("/report/preview", response_model=MatchingReport)
async def preview_report(
    inp: ReportInput,
    use_llm: bool = Query(default=True, description="False 면 LLM 없이 템플릿 서술로"),
) -> MatchingReport:
    return await build_report(inp, ReportAgent() if use_llm else None)


@router.get("/{simulation_id}", response_model=SimulationResponse)
async def get_simulation(simulation_id: str, service: SimulationService = Depends(get_service)) -> SimulationResponse:
    try:
        return await service.get(simulation_id)
    except SimulationNotFound as e:
        raise HTTPException(404, "simulation not found") from e


@router.get("/{simulation_id}/report", response_model=MatchingReport)
async def get_report(simulation_id: str, service: SimulationService = Depends(get_service)) -> MatchingReport:
    try:
        return await service.get_report(simulation_id)
    except SimulationNotFound as e:
        raise HTTPException(404, "simulation not found") from e
