"""라우터. 검증과 상태코드만 담당하고 로직은 service 로 넘긴다.

  POST /v1/persona-extraction/practice       : 연습대화 미반영 내 발화로 추출 (202, 비동기)
  POST /v1/persona-extraction/conversation   : 카카오톡 대화(파일 또는 텍스트)로 추출 (202, 비동기)
  GET  /v1/persona-extraction/jobs/{job_id}  : 작업 상태·결과
  DELETE /v1/persona-extraction/style        : 대화 스타일 삭제 및 온보딩 점수 복원 (200)

작업은 응답을 보낸 뒤 BackgroundTasks 로 실행한다. 요청 DB 세션은 응답과 함께 닫히므로 새 세션을 연다.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import NoReturn

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import SessionLocal, get_db
from app.features.persona.schemas import PersonaResponse

from .parsers import UnknownFormat
from .schemas import ExtractionJobCreated, ExtractionJobResponse, PracticeExtractionRequest, StyleDeleteRequest
from .service import (
    ExtractionService,
    JobInProgress,
    JobNotFound,
    NoStyleToDelete,
    NothingToExtract,
    PersonaNotFound,
    SpeakerNotFound,
    TooFewUtterances,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/persona-extraction", tags=["persona-extraction"])

Runner = Callable[[str], Awaitable[None]]
_DOMAIN_ERRORS = (
    PersonaNotFound,
    JobInProgress,
    NothingToExtract,
    SpeakerNotFound,
    TooFewUtterances,
    UnknownFormat,
    NoStyleToDelete,
)


# 일반 요청용 ExtractionService 의존성
def get_service(db: AsyncSession = Depends(get_db)) -> ExtractionService:
    return ExtractionService(db)


# 응답 뒤 작업을 실행한다. 실패는 service.run 이 작업에 기록한다 — 여기서는 예상 못 한 예외만 로그로
async def run_in_background(job_id: str) -> None:
    try:
        async with SessionLocal() as db:
            await ExtractionService(db).run(job_id)
    except Exception:
        logger.exception("persona extraction job %s crashed", job_id)


# 테스트가 가짜로 바꿀 수 있게 의존성으로 둔다
def get_runner() -> Runner:
    return run_in_background


# 도메인 예외 → HTTP 상태코드. 시작 라우트 두 개가 같이 쓴다
def _raise_http(e: Exception) -> NoReturn:
    if isinstance(e, PersonaNotFound):
        raise HTTPException(404, "확정된 페르소나가 없어요") from e
    if isinstance(e, JobInProgress):
        raise HTTPException(409, {"message": "이미 진행 중인 추출이 있어요", "job_id": e.job_id}) from e
    if isinstance(e, NothingToExtract):
        raise HTTPException(409, "반영할 새 연습대화 발화가 없어요") from e
    if isinstance(e, NoStyleToDelete):
        raise HTTPException(400, str(e)) from e
    if isinstance(e, SpeakerNotFound):
        raise HTTPException(422, f"대화에 '{e}' 화자가 없어요") from e
    if isinstance(e, TooFewUtterances):
        need = get_settings().extraction_min_utterances
        raise HTTPException(422, f"새 발화가 {e.count}개예요. 최소 {need}개가 필요해요") from e
    if isinstance(e, UnknownFormat):
        raise HTTPException(422, str(e)) from e
    raise e


# 업로드 본문을 문자열로. 파일과 텍스트 중 정확히 하나, 크기 제한, UTF-8(BOM 허용)
async def _read_upload(file: UploadFile | None, text: str | None) -> str:
    if (file is None) == (text is None):
        raise HTTPException(422, "file 또는 text 중 하나만 보내 주세요")
    limit = get_settings().extraction_max_upload_bytes
    if file is not None:
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(413, "파일이 너무 커요")
        try:
            return data.decode("utf-8-sig")  # 카카오톡 PC 내보내기는 BOM 이 붙는다
        except UnicodeDecodeError as e:
            raise HTTPException(422, "UTF-8 텍스트 파일만 받을 수 있어요") from e
    if len(text.encode()) > limit:
        raise HTTPException(413, "텍스트가 너무 길어요")
    return text


# 연습대화 미반영 발화로 추출 작업을 만든다. 50개 기준은 프론트가 관리한다
@router.post("/practice", response_model=ExtractionJobCreated, status_code=202)
async def extract_practice(
    req: PracticeExtractionRequest,
    background_tasks: BackgroundTasks,
    service: ExtractionService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
    runner: Runner = Depends(get_runner),
) -> ExtractionJobCreated:
    try:
        job = await service.start_practice(req.user_id)
    except _DOMAIN_ERRORS as e:
        _raise_http(e)
    await db.commit()
    background_tasks.add_task(runner, job.id)
    return ExtractionJobCreated(job_id=job.id, status="pending")


# 카카오톡 대화(파일 .txt 또는 text 중 하나)로 추출 작업을 만든다. speaker_name 의 발화만 본인 것으로 본다
@router.post("/conversation", response_model=ExtractionJobCreated, status_code=202)
async def extract_conversation(
    background_tasks: BackgroundTasks,
    user_id: str = Form(min_length=1, max_length=64),
    speaker_name: str = Form(min_length=1, max_length=40),
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    service: ExtractionService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
    runner: Runner = Depends(get_runner),
) -> ExtractionJobCreated:
    raw = await _read_upload(file, text)
    try:
        job = await service.start_conversation(user_id, speaker_name.strip(), raw)
    except _DOMAIN_ERRORS as e:
        _raise_http(e)
    await db.commit()
    background_tasks.add_task(runner, job.id)
    return ExtractionJobCreated(job_id=job.id, status="pending")


# 작업 상태·결과. 멈춘 작업은 조회하면서 failed 로 바뀔 수 있어 커밋한다
@router.get("/jobs/{job_id}", response_model=ExtractionJobResponse)
async def get_job(
    job_id: str,
    service: ExtractionService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> ExtractionJobResponse:
    try:
        job = await service.get_job(job_id)
    except JobNotFound as e:
        raise HTTPException(404, "job not found") from e
    await db.commit()
    return ExtractionJobResponse(
        job_id=job.id,
        kind=job.kind,
        status=job.status,
        persona_id=job.persona_id,
        analyzed_count=job.analyzed_count,
        error=job.error,
        created_at=job.created_at,
        finished_at=job.finished_at,
    )


# 대화 스타일 삭제(초기화). conversation_style=None 및 온보딩 점수 복원 새 버전을 즉시 만든다.
# 과거 발화 반영 마킹은 유지되어 이후 새 대화부터 추출된다 (2026-10-08).
@router.delete("/style", response_model=PersonaResponse, status_code=200)
async def delete_style(
    req: StyleDeleteRequest,
    service: ExtractionService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> PersonaResponse:
    try:
        resp = await service.delete_style(req.user_id)
    except _DOMAIN_ERRORS as e:
        _raise_http(e)
    await db.commit()
    return resp
