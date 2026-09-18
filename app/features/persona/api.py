"""라우터. 검증과 상태코드만 담당하고 로직은 service로 넘긴다."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db

from .agents import BuildFailed
from .repository import PersonaRepository
from .schemas import (
    AnswerRequest,
    FeedbackRequest,
    HistoryItem,
    PersonaResponse,
    StartRequest,
    SupplementRequest,
    TurnResponse,
)
from .service import NoPersonaYet, OnboardingService, TooFewAnswers, UnknownDimension

router = APIRouter(prefix="/v1/persona", tags=["persona"])


def get_service(db: AsyncSession = Depends(get_db)) -> OnboardingService:
    return OnboardingService(PersonaRepository(db))


@router.post("/onboarding/start", response_model=TurnResponse)
async def start(
    req: StartRequest,
    service: OnboardingService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> TurnResponse:
    result = await service.start(req.nickname, req.total_turns, req.user_id)
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


@router.post("/onboarding/{session_id}/skip", response_model=TurnResponse)
async def skip(
    session_id: str,
    service: OnboardingService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> TurnResponse:
    """이 질문 건너뛰기. 답한 턴이 3개 이상일 때만."""
    session = await service.repo.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    if session.pending_topic_id is None:
        raise HTTPException(409, "no pending question")

    try:
        result = await service.skip(session)
    except TooFewAnswers as e:
        raise HTTPException(409, f"need more answers before skipping ({e.answered} answered)") from e
    await db.commit()
    return result


@router.post("/onboarding/{session_id}/finish", response_model=TurnResponse)
async def finish(
    session_id: str,
    service: OnboardingService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> TurnResponse:
    """대화 여기서 끝내기. 답한 턴이 3개 이상일 때만. 이후 /build 로 페르소나 생성."""
    session = await service.repo.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")

    try:
        result = await service.finish(session)
    except TooFewAnswers as e:
        raise HTTPException(409, f"need more answers before finishing ({e.answered} answered)") from e
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


# ── 빌드 이후: 조회 · 보강 · 확인 · 이력 ─────────────────


async def _session_or_404(service: OnboardingService, session_id: str):
    session = await service.repo.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    return session


@router.get("/{session_id}", response_model=PersonaResponse)
async def get_persona(session_id: str, service: OnboardingService = Depends(get_service)) -> PersonaResponse:
    """최신 페르소나. (user_id 가 붙으면 /users/{user_id} 로도 열 것)"""
    session = await _session_or_404(service, session_id)
    try:
        return await service.get_latest(session)
    except NoPersonaYet as e:
        raise HTTPException(404, "persona not built yet") from e


@router.get("/{session_id}/history", response_model=list[HistoryItem])
async def history(session_id: str, service: OnboardingService = Depends(get_service)) -> list[HistoryItem]:
    """버전 목록. 최신 먼저."""
    session = await _session_or_404(service, session_id)
    return await service.history(session)


@router.post("/{session_id}/supplement", response_model=PersonaResponse)
async def supplement(
    session_id: str,
    req: SupplementRequest,
    service: OnboardingService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> PersonaResponse:
    """근거 부족한 차원에 답 하나 더 → 즉시 재빌드 → 새 버전 (changes 포함)."""
    session = await _session_or_404(service, session_id)
    try:
        result = await service.supplement(session, req.dimension, req.answer)
    except NoPersonaYet as e:
        raise HTTPException(409, "build first") from e
    except UnknownDimension as e:
        raise HTTPException(422, str(e)) from e
    except BuildFailed as e:
        await db.rollback()
        raise HTTPException(503, f"build failed: {e}") from e
    await db.commit()
    return result


@router.post("/{session_id}/feedback", response_model=PersonaResponse)
async def feedback(
    session_id: str,
    req: FeedbackRequest,
    service: OnboardingService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> PersonaResponse:
    """'이대로 좋아요'(agree) 또는 '조금 다른 것 같아요'(area 지정). 다르면 앱이 그 영역의 gaps 로 안내."""
    session = await _session_or_404(service, session_id)
    try:
        result = await service.feedback(session, req.agree, req.area)
    except NoPersonaYet as e:
        raise HTTPException(409, "build first") from e
    await db.commit()
    return result
