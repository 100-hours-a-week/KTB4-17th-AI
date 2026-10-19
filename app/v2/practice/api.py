"""/ai/api/v2/practice — v1 과 같은 계약, V2PracticeService + 응답 후 메모리 트리거."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.core.db import get_db
from app.features.practice.api import (
    _check_opening,
    _check_retry,
    _collect_reply,
    _require_active,
    _session_or_404,
    _stream_response,
)
from app.features.practice.schemas import (
    PracticeMessageRequest,
    PracticeReplyResponse,
    PracticeSessionResponse,
    PracticeSessionSummary,
    PracticeStartRequest,
    PracticeStartResponse,
)
from app.features.practice.service import PersonaNotFound
from app.v2.persona.service import maybe_run_memory_update

from .service import V2PracticeService

router = APIRouter(prefix="/v2/practice", tags=["v2_practice"])


def get_service(db: AsyncSession = Depends(get_db)) -> V2PracticeService:
    return V2PracticeService(db)


# SSE 는 응답 스트림이 끝날 때까지 DB 세션을 유지해야 해서 request scope (v1 과 동일)
def get_stream_service(db: AsyncSession = Depends(get_db, scope="request")) -> V2PracticeService:
    return V2PracticeService(db)


@router.post("/start", response_model=PracticeStartResponse, status_code=201)
async def start(
    req: PracticeStartRequest,
    service: V2PracticeService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> PracticeStartResponse:
    try:
        result = await service.start(req)
    except PersonaNotFound as e:
        raise HTTPException(404, f"{e.who}: 확정된 페르소나가 없어요 ({e.ref.describe()})") from e
    await db.commit()
    return result


@router.post("/{session_id}/opening", response_model=PracticeReplyResponse)
async def opening(session_id: str, service: V2PracticeService = Depends(get_service)) -> PracticeReplyResponse:
    session = await _session_or_404(service, session_id)
    _check_opening(session)
    return await _collect_reply(service.stream_opening(session))


@router.post("/{session_id}/messages", response_model=PracticeReplyResponse)
async def send_message(
    session_id: str,
    req: PracticeMessageRequest,
    background: BackgroundTasks,
    service: V2PracticeService = Depends(get_service),
) -> PracticeReplyResponse:
    session = await _session_or_404(service, session_id)
    _require_active(session)
    user_id, message = session.user_id, req.message.strip()
    reply = await _collect_reply(service.stream_reply(session, message))
    if user_id:
        background.add_task(maybe_run_memory_update, user_id, message)
    return reply


@router.post("/{session_id}/retry", response_model=PracticeReplyResponse)
async def retry(session_id: str, service: V2PracticeService = Depends(get_service)) -> PracticeReplyResponse:
    session = await _session_or_404(service, session_id)
    _check_retry(session)
    return await _collect_reply(service.stream_retry(session))


@router.post("/{session_id}/opening/stream")
async def opening_stream(
    session_id: str, service: V2PracticeService = Depends(get_stream_service)
) -> StreamingResponse:
    session = await _session_or_404(service, session_id)
    _check_opening(session)
    return _stream_response(service.stream_opening(session))


@router.post("/{session_id}/messages/stream")
async def send_message_stream(
    session_id: str,
    req: PracticeMessageRequest,
    service: V2PracticeService = Depends(get_stream_service),
) -> StreamingResponse:
    session = await _session_or_404(service, session_id)
    _require_active(session)
    user_id, message = session.user_id, req.message.strip()
    response = _stream_response(service.stream_reply(session, message))
    if user_id:  # 스트림이 모두 전송된 뒤 실행된다
        response.background = BackgroundTask(maybe_run_memory_update, user_id, message)
    return response


@router.post("/{session_id}/retry/stream")
async def retry_stream(session_id: str, service: V2PracticeService = Depends(get_stream_service)) -> StreamingResponse:
    session = await _session_or_404(service, session_id)
    _check_retry(session)
    return _stream_response(service.stream_retry(session))


@router.get("", response_model=list[PracticeSessionSummary])
async def list_sessions(
    user_id: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=50, ge=1, le=100),
    service: V2PracticeService = Depends(get_service),
) -> list[PracticeSessionSummary]:
    return await service.list_for_user(user_id, limit)


@router.get("/{session_id}", response_model=PracticeSessionResponse)
async def get_session(session_id: str, service: V2PracticeService = Depends(get_service)) -> PracticeSessionResponse:
    session = await _session_or_404(service, session_id)
    return await service.get(session)


@router.post("/{session_id}/end", response_model=PracticeSessionResponse)
async def end_session(
    session_id: str,
    service: V2PracticeService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> PracticeSessionResponse:
    session = await _session_or_404(service, session_id)
    result = await service.end(session)
    await db.commit()
    return result
