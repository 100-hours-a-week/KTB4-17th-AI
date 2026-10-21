"""/ai/api/v2/practice — v1 과 같은 계약, V2PracticeService + 응답 후 메모리 트리거.

SSE 직렬화·사전 검사·예외→상태코드 변환은 v1 app/features/practice/api.py 에서 복제했다.
요청/응답 스키마만 v1 과 공유한다.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.core.db import get_db
from app.features.practice.schemas import (
    PracticeMessageRequest,
    PracticeReplyResponse,
    PracticeSessionResponse,
    PracticeSessionSummary,
    PracticeStartRequest,
    PracticeStartResponse,
)
from app.v2.persona.service import maybe_run_memory_update

from .service import (
    CONCURRENT_DETAIL,
    ConcurrentRequest,
    Event,
    NothingToRetry,
    OpeningAlreadyDone,
    PersonaNotFound,
    ReplyFailed,
    SessionEnded,
    V2PracticeService,
    collect_reply,
)

router = APIRouter(prefix="/v2/practice", tags=["v2_practice"])


SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # nginx 가 버퍼링하지 않게
}


# SSE 한 프레임(event/data)을 스펙 형식 문자열로 포맷
def _sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


# service 가 낸 (이벤트, 모델) 스트림을 SSE 텍스트 스트림으로 직렬화하고, 도메인 예외를 error 이벤트로 변환
async def _to_sse(events: AsyncIterator[Event]) -> AsyncIterator[str]:
    try:
        async for name, data in events:
            yield _sse(name, data.model_dump_json())
    except SessionEnded:
        yield _sse("error", json.dumps({"detail": "session ended"}, ensure_ascii=False))
    except PersonaNotFound as e:
        yield _sse("error", json.dumps({"detail": f"{e.who}: 확정된 페르소나가 없어요"}, ensure_ascii=False))
    except NothingToRetry:
        yield _sse("error", json.dumps({"detail": "nothing to retry"}, ensure_ascii=False))
    except OpeningAlreadyDone:
        yield _sse("error", json.dumps({"detail": "opening already done"}, ensure_ascii=False))
    except ConcurrentRequest:
        yield _sse("error", json.dumps({"detail": CONCURRENT_DETAIL}, ensure_ascii=False))


# SSE 텍스트 스트림에 미디어 타입·헤더를 씌워 StreamingResponse 로 감싼다
def _stream_response(events: AsyncIterator[Event]) -> StreamingResponse:
    return StreamingResponse(_to_sse(events), media_type="text/event-stream", headers=SSE_HEADERS)


# session_id 로 세션을 조회하고, 없으면 404
async def _session_or_404(service: V2PracticeService, session_id: str):
    session = await service.repo.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    return session


# 종료된 세션이면 409
def _require_active(session) -> None:
    if session.status != "active":
        raise HTTPException(409, "session ended")


# opening 은 세션당 한 번 — 종료됐거나 이미 메시지가 있으면 409
def _check_opening(session) -> None:
    _require_active(session)
    if session.messages:
        raise HTTPException(409, "opening already done")


# retry 는 마지막이 답을 못 받은 내 메시지일 때만
def _check_retry(session) -> None:
    _require_active(session)
    if not session.messages or session.messages[-1].role != "user":
        raise HTTPException(409, "nothing to retry")


# 스트림을 끝까지 모아 JSON 한 번으로 돌려준다. 스트림 안에서 나는 도메인 예외는 HTTP 상태코드로 바꾼다
async def _collect_reply(events: AsyncIterator[Event]) -> PracticeReplyResponse:
    try:
        done = await collect_reply(events)
    except SessionEnded as e:
        raise HTTPException(409, "session ended") from e
    except NothingToRetry as e:
        raise HTTPException(409, "nothing to retry") from e
    except OpeningAlreadyDone as e:
        raise HTTPException(409, "opening already done") from e
    except ConcurrentRequest as e:
        raise HTTPException(409, CONCURRENT_DETAIL) from e
    except PersonaNotFound as e:
        raise HTTPException(404, f"{e.who}: 확정된 페르소나가 없어요") from e
    except ReplyFailed as e:
        # 내 메시지는 이미 저장돼 있다 — /retry 로 답변만 다시 받을 수 있다
        raise HTTPException(503, f"답변 도중 연결이 끊겼어요: {e}") from e
    return PracticeReplyResponse(**done.model_dump())


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
