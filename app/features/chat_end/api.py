"""라우터. 검증과 커밋만 담당하고 로직은 service 로 넘긴다.

POST /v1/chat_end/drafts                    : 1·2·3번째 답변 스크립트 (동기 200, PENDING 으로 저장)
POST /v1/chat_end/drafts/{draft_id}/confirm : 스크립트 확정 (200, 멱등. 없으면 404, messages 시작 후 다른 초안이면 409)
POST /v1/chat_end/messages                  : 확정 스크립트의 N번째 문장을 방향으로 삼은 종료 메시지
                                              (동기 200. LLM 장애·가드레일 차단으로 못 만들면 503 — 폴백 문장은 내리지 않는다.
                                              확정 스크립트가 없거나 요청 end_type 이 확정본과 다르면 409)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db

from .schemas import (
    ChatEndConfirmResponse,
    ChatEndDraftsRequest,
    ChatEndDraftsResponse,
    ChatEndMessageRequest,
    ChatEndMessageResponse,
)
from .service import (
    ChatEndService,
    DraftLocked,
    DraftNotFound,
    EndTypeMismatch,
    MessageGenerationFailed,
    NoConfirmedDraft,
)

router = APIRouter(prefix="/v1/chat_end", tags=["chat-end"])


# ChatEndService 의존성 — DB 세션은 function scope
def get_service(db: AsyncSession = Depends(get_db)) -> ChatEndService:
    return ChatEndService(db)


@router.post("/drafts", response_model=ChatEndDraftsResponse, summary="관계 마무리 3단계 답변 스크립트 생성")
async def create_drafts(
    req: ChatEndDraftsRequest,
    service: ChatEndService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> ChatEndDraftsResponse:
    result = await service.create_drafts(req)
    await db.commit()
    return result


@router.post(
    "/drafts/{draft_id}/confirm",
    response_model=ChatEndConfirmResponse,
    summary="스크립트 확정",
    responses={
        404: {"description": "없는 draft_id"},
        409: {"description": "이 위임에서 messages 가 이미 성공해 다른 초안으로 바꿀 수 없음"},
    },
)
async def confirm_draft(
    draft_id: str = Path(pattern=r"^[0-9a-f]{32}$", description="drafts 응답의 draft_id (32자리 소문자 hex)"),
    service: ChatEndService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> ChatEndConfirmResponse:
    try:
        result = await service.confirm_draft(draft_id)
    except DraftNotFound as e:
        raise HTTPException(404, "초안을 찾을 수 없어요") from e
    except DraftLocked as e:
        raise HTTPException(409, "messages 가 이미 시작되어 초안을 바꿀 수 없어요") from e
    await db.commit()
    return result


@router.post(
    "/messages",
    response_model=ChatEndMessageResponse,
    summary="확정 스크립트를 기반으로 종료 채팅 메시지 생성",
    responses={
        409: {"description": "확정된 초안이 없거나, 요청의 end_type 이 확정본과 다름"},
        503: {
            "description": "LLM 장애·가드레일 차단으로 메시지를 만들지 못함. 턴을 쓰지 않으니 같은 요청을 다시 보내면 된다"
        },
    },
)
async def create_message(
    req: ChatEndMessageRequest,
    service: ChatEndService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> ChatEndMessageResponse:
    try:
        result = await service.create_message(req)
    except NoConfirmedDraft as e:
        raise HTTPException(409, "확정된 초안이 없어요. 먼저 초안을 만들고 확정해 주세요") from e
    except EndTypeMismatch as e:
        raise HTTPException(
            409, f"확정한 초안의 종료 유형({e.confirmed})과 요청의 end_type({e.requested})이 달라요"
        ) from e
    except MessageGenerationFailed as e:
        await db.commit()  # 실패 행과 가드레일 추적을 남긴다
        raise HTTPException(503, "종료 메시지를 만들지 못했어요. 잠시 후 다시 시도해 주세요") from e
    await db.commit()
    return result
