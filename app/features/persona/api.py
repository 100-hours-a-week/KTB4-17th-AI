"""라우터. 검증과 상태코드만 담당하고 로직은 service로 넘긴다."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db

from .agents import BuildFailed
from .repository import PersonaRepository, SessionBusy
from .schemas import (
    REQUEST_IN_PROGRESS,
    TURN_MISMATCH,
    AnswerRequest,
    ConfirmPersonaRequest,
    ConfirmPersonaResponse,
    PersonaResponse,
    StartRequest,
    SupplementRequest,
    TurnResponse,
    answer_problem,
)
from .service import (
    NoPersonaYet,
    OnboardingNotFinished,
    OnboardingService,
    PersonaAlreadyConfirmed,
    PersonaConfirmationConflict,
    PersonaDraftNotFound,
    TooFewAnswers,
    TurnMismatch,
    UnknownDimension,
)

router = APIRouter(prefix="/v1/persona", tags=["persona"])


def get_service(db: AsyncSession = Depends(get_db)) -> OnboardingService:
    return OnboardingService(PersonaRepository(db))


@router.post("/onboarding/start", response_model=TurnResponse)
async def start(
    req: StartRequest,
    service: OnboardingService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> TurnResponse:
    result = await service.start(req.nickname, req.user_id, req.mbti)
    await db.commit()
    return result


# 답변·건너뛰기·끝내기 공통 전처리. 같은 세션의 요청을 처리 중이면 기다리지 않고 409, 없으면 404
async def _lock_or_409(service: OnboardingService, session_id: str):
    try:
        session = await service.repo.lock_session(session_id)
    except SessionBusy as e:
        raise HTTPException(409, REQUEST_IN_PROGRESS) from e
    if session is None:
        raise HTTPException(404, "session not found")
    return session


@router.post("/onboarding/{session_id}/answer", response_model=TurnResponse)
async def answer(
    session_id: str,
    req: AnswerRequest,
    service: OnboardingService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> TurnResponse:
    # 빈 답·너무 긴 답은 질문을 소모하지 않고 422. detail.message 를 그대로 띄우면 사용자가 다시 보낼 수 있다
    problem = answer_problem(req.answer)
    if problem is not None:
        raise HTTPException(422, problem)

    session = await _lock_or_409(service, session_id)
    # 이미 지난 턴에 대한 답(재전송)이면 끝난 세션이어도 마지막 응답을 다시 돌려준다
    resend = req.turn_index is not None and req.turn_index < session.turn_index
    if session.pending_topic_id is None and not resend:
        raise HTTPException(409, "no pending question")

    try:
        result = await service.submit_answer(session, req.answer, turn_index=req.turn_index)
        await db.commit()
    except TurnMismatch as e:
        raise HTTPException(409, TURN_MISMATCH) from e
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(409, REQUEST_IN_PROGRESS) from e
    return result


@router.post("/onboarding/{session_id}/skip", response_model=TurnResponse)
async def skip(
    session_id: str,
    service: OnboardingService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> TurnResponse:
    """이 질문 건너뛰기. 답한 턴이 3개 이상일 때만."""
    session = await _lock_or_409(service, session_id)
    if session.pending_topic_id is None:
        raise HTTPException(409, "no pending question")

    try:
        result = await service.skip(session)
        await db.commit()
    except TooFewAnswers as e:
        raise HTTPException(409, f"need more answers before skipping ({e.answered} answered)") from e
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(409, REQUEST_IN_PROGRESS) from e
    return result


@router.post("/onboarding/{session_id}/finish", response_model=TurnResponse)
async def finish(
    session_id: str,
    service: OnboardingService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> TurnResponse:
    """대화 여기서 끝내기. 답한 턴이 3개 이상일 때만. 이후 /build 로 페르소나 생성."""
    session = await _lock_or_409(service, session_id)

    try:
        result = await service.finish(session)
        await db.commit()
    except TooFewAnswers as e:
        raise HTTPException(409, f"need more answers before finishing ({e.answered} answered)") from e
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(409, REQUEST_IN_PROGRESS) from e
    return result


@router.post("/{session_id}/build", response_model=PersonaResponse)
async def build(
    session_id: str,
    service: OnboardingService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> PersonaResponse:
    session = await service.repo.get_session_for_update(session_id)
    if session is None:
        raise HTTPException(404, "session not found")

    try:
        result = await service.build_draft(session)
    except OnboardingNotFinished as e:
        raise HTTPException(409, "onboarding is not finished") from e
    except PersonaAlreadyConfirmed as e:
        raise HTTPException(409, "persona is already confirmed") from e
    except BuildFailed as e:
        await db.rollback()
        # 세션은 남는다 — 재시도 가능해야 하므로
        raise HTTPException(503, f"build failed: {e}") from e

    await db.commit()
    return result


@router.post("/{persona_id}/confirm", response_model=ConfirmPersonaResponse)
async def confirm(
    persona_id: str,
    req: ConfirmPersonaRequest,
    service: OnboardingService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> ConfirmPersonaResponse:
    """`/build`가 만든 가치관 초안을 확정하고 같은 user_id에 MBTI를 저장한다."""
    if persona_id != req.persona_id:
        raise HTTPException(409, "persona_id in path and body do not match")

    try:
        result = await service.confirm_persona(persona_id, req.mbti, req.confirmed_at)
    except PersonaDraftNotFound as e:
        raise HTTPException(404, "persona draft not found") from e
    except PersonaConfirmationConflict as e:
        raise HTTPException(409, "persona is already confirmed with different data") from e

    await db.commit()
    return result


# ── 빌드 이후: 조회 · 보강  ─────────────────


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
