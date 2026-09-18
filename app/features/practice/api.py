"""라우터. 검증과 상태코드만 담당하고 로직은 service 로 넘긴다.

  POST /v1/practice/start                 : 상대 페르소나 골라 세션 열기 (JSON)
  POST /v1/practice/{id}/opening          : 상대가 먼저 인사 (SSE)
  POST /v1/practice/{id}/messages         : 내 메시지 → 상대 답변 (SSE)
  GET  /v1/practice/{id}                  : 세션 + 전체 메시지
  POST /v1/practice/{id}/end              : 세션 닫기

SSE 라우트의 DB 세션은 scope="request" — 기본(function) 이면 라우트 함수가 돌아온 순간 세션이 닫혀서
스트리밍 중에 저장할 수 없다. request 스코프는 응답(스트림)이 끝난 뒤에 닫는다.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db

from .schemas import (
    PracticeMessageRequest,
    PracticeSessionResponse,
    PracticeStartRequest,
    PracticeStartResponse,
)
from .service import Event, PersonaNotFound, PracticeService, SessionEnded

router = APIRouter(prefix="/v1/practice", tags=["practice"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # nginx 가 버퍼링하지 않게
}


def get_service(db: AsyncSession = Depends(get_db)) -> PracticeService:
    return PracticeService(db)


def get_stream_service(db: AsyncSession = Depends(get_db, scope="request")) -> PracticeService:
    return PracticeService(db)


def _sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


async def _to_sse(events: AsyncIterator[Event]) -> AsyncIterator[str]:
    try:
        async for name, data in events:
            yield _sse(name, data.model_dump_json())
    except SessionEnded:
        yield _sse("error", json.dumps({"detail": "session ended"}, ensure_ascii=False))
    except PersonaNotFound as e:
        yield _sse("error", json.dumps({"detail": f"{e.who}: 페르소나가 없어요"}, ensure_ascii=False))


def _stream_response(events: AsyncIterator[Event]) -> StreamingResponse:
    return StreamingResponse(_to_sse(events), media_type="text/event-stream", headers=SSE_HEADERS)


async def _session_or_404(service: PracticeService, session_id: str):
    session = await service.repo.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    return session


@router.post("/start", response_model=PracticeStartResponse, status_code=201)
async def start(
    req: PracticeStartRequest,
    service: PracticeService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> PracticeStartResponse:
    try:
        result = await service.start(req)
    except PersonaNotFound as e:
        raise HTTPException(404, f"{e.who}: 저장된 페르소나가 없어요 ({e.ref.describe()})") from e
    await db.commit()
    return result


@router.post("/{session_id}/opening")
async def opening(session_id: str, service: PracticeService = Depends(get_stream_service)) -> StreamingResponse:
    """상대 페르소나가 먼저 말을 건다. SSE: start → delta… → done."""
    session = await _session_or_404(service, session_id)
    if session.status != "active":
        raise HTTPException(409, "session ended")
    return _stream_response(service.stream_opening(session))


@router.post("/{session_id}/messages")
async def send_message(
    session_id: str,
    req: PracticeMessageRequest,
    service: PracticeService = Depends(get_stream_service),
) -> StreamingResponse:
    """내 메시지를 보내고 상대 답변을 받는다. SSE: start → delta… → done (또는 error)."""
    session = await _session_or_404(service, session_id)
    if session.status != "active":
        raise HTTPException(409, "session ended")
    return _stream_response(service.stream_reply(session, req.message.strip()))


@router.get("/{session_id}", response_model=PracticeSessionResponse)
async def get_session(session_id: str, service: PracticeService = Depends(get_service)) -> PracticeSessionResponse:
    session = await _session_or_404(service, session_id)
    return await service.get(session)


@router.post("/{session_id}/end", response_model=PracticeSessionResponse)
async def end_session(
    session_id: str,
    service: PracticeService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> PracticeSessionResponse:
    session = await _session_or_404(service, session_id)
    result = await service.end(session)
    await db.commit()
    return result
