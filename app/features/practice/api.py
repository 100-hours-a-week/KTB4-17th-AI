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


# 일반(JSON) 요청용 PracticeService 의존성 — DB 세션은 function scope
def get_service(db: AsyncSession = Depends(get_db)) -> PracticeService:
    return PracticeService(db)


# SSE 스트리밍 요청용 PracticeService 의존성 — 응답 스트림이 끝날 때까지 DB 세션을 유지해야 해서 request scope
def get_stream_service(db: AsyncSession = Depends(get_db, scope="request")) -> PracticeService:
    return PracticeService(db)


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
        yield _sse("error", json.dumps({"detail": f"{e.who}: 페르소나가 없어요"}, ensure_ascii=False))


# SSE 텍스트 스트림에 미디어 타입·헤더를 씌워 StreamingResponse 로 감싼다
def _stream_response(events: AsyncIterator[Event]) -> StreamingResponse:
    return StreamingResponse(_to_sse(events), media_type="text/event-stream", headers=SSE_HEADERS)


# session_id 로 세션을 조회하고, 없으면 404 — opening/messages/get/end 라우트 공통 전처리
async def _session_or_404(service: PracticeService, session_id: str):
    session = await service.repo.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    return session


# 새 연습대화 세션을 연다 — 상대(+선택적으로 내) 페르소나를 골라 세션 row 를 만든다
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


# 상대 페르소나가 먼저 말을 거는 첫 메시지를 스트리밍으로 받는다
@router.post("/{session_id}/opening")
async def opening(session_id: str, service: PracticeService = Depends(get_stream_service)) -> StreamingResponse:
    """상대 페르소나가 먼저 말을 건다. SSE: start → delta… → done."""
    session = await _session_or_404(service, session_id)
    if session.status != "active":
        raise HTTPException(409, "session ended")
    return _stream_response(service.stream_opening(session))


# 내가 보낸 메시지를 저장하고 상대 답변을 스트리밍으로 받는다. session_id 는 미리 만들어져 있어야 한다(지금은 /start)
@router.post(
    "/{session_id}/messages",
    summary="⭐ [사용 중] 메시지 전송 및 상대 답변 스트리밍",
    description="""
상대 페르소나와 메시지를 주고받는 실시간 SSE 스트리밍 엔드포인트입니다. 모든 대화 내역은 이 서버가 DB에 저장합니다.
`session_id`는 미리 만들어져 있어야 하며(지금은 `/start`), 없는 `session_id`로 호출하면 404 에러가 납니다.
""",
)
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


# 세션과 지금까지의 전체 메시지를 조회
@router.get("/{session_id}", response_model=PracticeSessionResponse)
async def get_session(session_id: str, service: PracticeService = Depends(get_service)) -> PracticeSessionResponse:
    session = await _session_or_404(service, session_id)
    return await service.get(session)


# 세션을 종료 상태로 닫는다
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
