"""라우터. 검증과 상태코드만 담당하고 로직은 service로 넘긴다."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from .agents import BuildFailed
from .repository import PersonaRepository
from .schemas import AnswerRequest, PersonaResponse, StartRequest, TurnResponse
from .service import OnboardingService

router = APIRouter(prefix="/v1/persona", tags=["persona"])


async def get_db() -> AsyncSession:  # 프로젝트 공통 의존성으로 교체
    raise NotImplementedError


def get_service(db: AsyncSession = Depends(get_db)) -> OnboardingService:
    return OnboardingService(PersonaRepository(db))


@router.post("/onboarding/start", response_model=TurnResponse)
async def start(
    req: StartRequest,
    service: OnboardingService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> TurnResponse:
    result = await service.start(req.nickname, req.total_turns)
    await db.commit()
    return result


@router.post("/onboarding/{session_id}/answer", response_model=TurnResponse)
async def answer(
    session_id: str,
    req: AnswerRequest,
    service: OnboardingService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> TurnResponse:
    session = await service.repo.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    if session.pending_topic_id is None:
        raise HTTPException(409, "no pending question")

    result = await service.submit_answer(session, req.answer)
    await db.commit()
    return result


@router.post("/{session_id}/build", response_model=PersonaResponse)
async def build(
    session_id: str,
    service: OnboardingService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> PersonaResponse:
    session = await service.repo.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")

    try:
        result = await service.build_persona(session)
    except BuildFailed as e:
        await db.rollback()
        # 세션은 남는다 — 재시도 가능해야 하므로
        raise HTTPException(503, f"build failed: {e}") from e

    await db.commit()
    return result
