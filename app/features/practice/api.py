"""라우터. 검증과 상태코드만 담당하고 로직은 service 로 넘긴다.

  POST /v1/practice/start                 : 상대 페르소나 골라 세션 열기 (JSON)
  POST /v1/practice/{id}/opening          : 상대가 먼저 인사 (JSON, 답변을 모아서 한 번에)
  POST /v1/practice/{id}/messages         : 내 메시지 → 상대 답변 (JSON)
  POST /v1/practice/{id}/retry            : 답변이 끊긴 내 마지막 메시지에 답변만 다시 (JSON)
  POST /v1/practice/{id}/opening/stream   : 위 세 개의 스트리밍(SSE) 버전
  POST /v1/practice/{id}/messages/stream
  POST /v1/practice/{id}/retry/stream
  GET  /v1/practice?user_id=…             : 내 연습대화 목록
  GET  /v1/practice/{id}                  : 세션 + 전체 메시지
  POST /v1/practice/{id}/end              : 세션 닫기

스트리밍(SSE) 라우트의 DB 세션은 scope="request" — 기본(function) 이면 라우트 함수가 돌아온 순간 세션이 닫혀서
스트리밍 중에 저장할 수 없다. request 스코프는 응답(스트림)이 끝난 뒤에 닫는다.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db

from .schemas import (
    PracticeMessageRequest,
    PracticeReplyResponse,
    PracticeSessionResponse,
    PracticeSessionSummary,
    PracticeStartRequest,
    PracticeStartResponse,
)
from .service import (
    CONCURRENT_DETAIL,
    ConcurrentRequest,
    Event,
    NothingToRetry,
    OpeningAlreadyDone,
    PersonaNotFound,
    PracticeService,
    ReplyFailed,
    SessionEnded,
    collect_reply,
)

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
        raise HTTPException(404, f"{e.who}: 확정된 페르소나가 없어요 ({e.ref.describe()})") from e
    await db.commit()
    return result


# ── 사전 검사 — 일반/스트리밍 라우트가 같은 규칙을 쓴다 ─────────────────


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


# ── 일반(JSON) — 답변을 다 만든 뒤 한 번에 돌려준다. 기본 경로 ──────────────


# 상대 페르소나가 먼저 말을 거는 첫 메시지를 한 번에 받는다
@router.post("/{session_id}/opening", response_model=PracticeReplyResponse)
async def opening(session_id: str, service: PracticeService = Depends(get_service)) -> PracticeReplyResponse:
    """상대 페르소나가 먼저 말을 건다. 답변이 다 만들어진 뒤 JSON 한 번으로 응답."""
    session = await _session_or_404(service, session_id)
    _check_opening(session)
    return await _collect_reply(service.stream_opening(session))


# 내 메시지를 저장하고 상대 답변을 한 번에 받는다. session_id 는 미리 만들어져 있어야 한다(지금은 /start)
@router.post(
    "/{session_id}/messages",
    response_model=PracticeReplyResponse,
    summary="메시지 전송 (답변을 모아서 한 번에)",
    description="""
상대 페르소나와 메시지를 주고받습니다. 답변이 다 만들어진 뒤 JSON 한 번으로 응답합니다. 실시간 표시가 필요하면 `/messages/stream`을 쓰세요.
모든 대화 내역은 이 서버가 DB에 저장합니다. 없는 `session_id`는 404입니다.
답변이 도중에 실패하면 503이며, 내 메시지는 저장돼 있으므로 `/retry`로 답변만 다시 받습니다.
""",
)
async def send_message(
    session_id: str,
    req: PracticeMessageRequest,
    service: PracticeService = Depends(get_service),
) -> PracticeReplyResponse:
    session = await _session_or_404(service, session_id)
    _require_active(session)
    return await _collect_reply(service.stream_reply(session, req.message.strip()))


# 답변이 도중에 끊겼을 때, 이미 저장된 내 마지막 메시지에 대한 상대 답변만 다시 받는다
@router.post("/{session_id}/retry", response_model=PracticeReplyResponse)
async def retry(session_id: str, service: PracticeService = Depends(get_service)) -> PracticeReplyResponse:
    """메시지를 다시 보내지 않고 답변만 다시 받는다. 마지막 메시지가 답을 못 받은 내 메시지일 때만."""
    session = await _session_or_404(service, session_id)
    _check_retry(session)
    return await _collect_reply(service.stream_retry(session))


# ── 스트리밍(SSE) — 같은 동작을 실시간 조각으로 받는다 ──────────────────────


# 상대 페르소나가 먼저 말을 거는 첫 메시지를 스트리밍으로 받는다
@router.post("/{session_id}/opening/stream")
async def opening_stream(session_id: str, service: PracticeService = Depends(get_stream_service)) -> StreamingResponse:
    """상대 페르소나가 먼저 말을 건다. SSE: start → delta… → done."""
    session = await _session_or_404(service, session_id)
    _check_opening(session)
    return _stream_response(service.stream_opening(session))


# 내가 보낸 메시지를 저장하고 상대 답변을 스트리밍으로 받는다
@router.post(
    "/{session_id}/messages/stream",
    summary="메시지 전송 및 상대 답변 스트리밍 (SSE)",
    description="""
`/messages`와 같은 동작을 SSE(`text/event-stream`)로 받습니다. `start → delta… → done`, 실패 시 `error` 이벤트.
스트림 도중 오류는 HTTP 상태코드가 아니라 `error` 이벤트로 옵니다. 없는 `session_id`는 404입니다.
""",
)
async def send_message_stream(
    session_id: str,
    req: PracticeMessageRequest,
    service: PracticeService = Depends(get_stream_service),
) -> StreamingResponse:
    session = await _session_or_404(service, session_id)
    _require_active(session)
    return _stream_response(service.stream_reply(session, req.message.strip()))


# 답변만 다시 받기 — 스트리밍 버전
@router.post("/{session_id}/retry/stream")
async def retry_stream(session_id: str, service: PracticeService = Depends(get_stream_service)) -> StreamingResponse:
    """메시지를 다시 보내지 않고 답변만 다시 받는다. SSE."""
    session = await _session_or_404(service, session_id)
    _check_retry(session)
    return _stream_response(service.stream_retry(session))


# 내 연습대화 목록 — me_user_id 로 시작한 세션만 나온다
@router.get("", response_model=list[PracticeSessionSummary])
async def list_sessions(
    user_id: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=50, ge=1, le=100),
    service: PracticeService = Depends(get_service),
) -> list[PracticeSessionSummary]:
    return await service.list_for_user(user_id, limit)


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
