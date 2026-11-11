"""라우터. 검증과 커밋만 담당하고 로직은 service 로 넘긴다.

POST /v1/sentence-cards/next          : 카드 1장 (200). 줄 카드가 없으면 204. 같은 request_id 는 같은 카드
POST /v1/sentence-cards/answers/batch : 답변 묶음 (200, 일부 거절돼도 200). 같은 batch_id 에 다른 내용이면 409
                                        응답을 보낸 뒤 밀린 학습 후보 알림을 백그라운드로 보낸다 (notify.py)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal, get_db
from app.core.security import require_internal_key

from .notify import discord_sender, notify_pending
from .schemas import (
    SentenceCardAnswerBatchRequest,
    SentenceCardAnswerBatchResponse,
    SentenceCardNextRequest,
    SentenceCardResponse,
)
from .service import BatchConflict, RequestIdConflict, SentenceCardService

router = APIRouter(
    prefix="/v1/sentence-cards",
    tags=["sentence-cards"],
    dependencies=[Depends(require_internal_key)],
)


def get_service(db: AsyncSession = Depends(get_db)) -> SentenceCardService:
    return SentenceCardService(db)


def get_notifier() -> Callable[[], Awaitable[int]]:
    # 요청 세션은 응답과 함께 닫히므로 알림은 자기 세션을 연다
    return partial(notify_pending, SessionLocal, discord_sender())


@router.post(
    "/next",
    response_model=SentenceCardResponse,
    summary="문장 판정 카드 1장 받기",
    responses={
        204: {"description": "줄 카드가 없음 (문장 없음 · 하루 상한 도달)"},
        409: {"description": "같은 request_id 가 다른 유저의 요청에 쓰였음"},
    },
)
async def next_card(
    req: SentenceCardNextRequest,
    service: SentenceCardService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> SentenceCardResponse | Response:
    try:
        card = await service.next_card(req)
    except RequestIdConflict as e:
        raise HTTPException(409, "request_id reused with different payload") from e
    await db.commit()
    if card is None:
        return Response(status_code=204)
    return card


@router.post(
    "/answers/batch",
    response_model=SentenceCardAnswerBatchResponse,
    summary="BE 가 모아 둔 답변 묶음 받기",
    responses={409: {"description": "같은 batch_id 에 다른 내용"}},
)
async def submit_answers(
    req: SentenceCardAnswerBatchRequest,
    background: BackgroundTasks,
    service: SentenceCardService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
    notifier: Callable[[], Awaitable[int]] = Depends(get_notifier),
) -> SentenceCardAnswerBatchResponse:
    try:
        result = await service.submit_batch(req)
    except BatchConflict as e:
        raise HTTPException(409, "batch_id reused with different payload") from e
    await db.commit()
    background.add_task(notifier)
    return result
