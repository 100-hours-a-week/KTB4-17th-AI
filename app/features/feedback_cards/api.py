from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import require_internal_key

from .schemas import (
    FeedbackCardAnswerRequest,
    FeedbackCardAnswerResponse,
    FeedbackCardNextRequest,
    FeedbackCardResponse,
    Identifier,
)
from .service import CardConflict, CardNotFound, FeedbackCardService, NotCardOwner


def require_feedback_key(x_internal_api_key: str | None = Header(default=None)) -> None:
    # 이 계약은 키 설정이 빠진 환경에서도 인증을 생략하지 않는다.
    if not get_settings().internal_api_key:
        raise HTTPException(401, "invalid internal api key")
    require_internal_key(x_internal_api_key)


router = APIRouter(
    prefix="/v1/feedback-cards",
    tags=["feedback-cards"],
    dependencies=[Depends(require_feedback_key)],
    responses={401: {"description": "내부 API 키 불일치"}},
)


def get_service(db: AsyncSession = Depends(get_db)) -> FeedbackCardService:
    return FeedbackCardService(db)


@router.post(
    "/next",
    response_model=FeedbackCardResponse,
    summary="본인 확인 카드 1장 받기",
    responses={204: {"description": "카드 없음 (본문 없음)"}},
)
async def next_card(
    req: FeedbackCardNextRequest,
    service: FeedbackCardService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> FeedbackCardResponse | Response:
    card = await service.next_card(req.user_id)
    await db.commit()
    return card if card is not None else Response(status_code=204)


@router.post(
    "/{card_id}/answer",
    response_model=FeedbackCardAnswerResponse,
    summary="본인 확인 카드에 답변하기",
    responses={
        403: {"description": "카드 주인이 아님"},
        404: {"description": "카드 없음"},
        409: {"description": "card expired 또는 card already answered"},
    },
)
async def answer_card(
    card_id: Annotated[Identifier, Path()],
    req: FeedbackCardAnswerRequest,
    service: FeedbackCardService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> FeedbackCardAnswerResponse:
    try:
        result = await service.answer(card_id, req)
    except CardNotFound as e:
        raise HTTPException(404, "card not found") from e
    except NotCardOwner as e:
        raise HTTPException(403, "not card owner") from e
    except CardConflict as e:
        raise HTTPException(409, str(e)) from e
    await db.commit()
    return result
