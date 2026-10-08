"""라우터. 검증과 커밋만 담당하고 로직은 service 로 넘긴다.

POST /v1/chat_end/drafts   : 관계 마무리 초안 3개 (동기 200)
POST /v1/chat_end/messages : 고른 초안을 방향으로 삼은 최종 종료 메시지 (동기 200, 실패해도 status=FAILED 로 200)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db

from .schemas import ChatEndDraftsRequest, ChatEndDraftsResponse, ChatEndMessageRequest, ChatEndMessageResponse
from .service import ChatEndService

router = APIRouter(prefix="/v1/chat_end", tags=["chat-end"])


# ChatEndService 의존성 — DB 세션은 function scope
def get_service(db: AsyncSession = Depends(get_db)) -> ChatEndService:
    return ChatEndService(db)


@router.post("/drafts", response_model=ChatEndDraftsResponse, summary="관계 마무리 초안 3개 생성")
async def create_drafts(
    req: ChatEndDraftsRequest,
    service: ChatEndService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> ChatEndDraftsResponse:
    result = await service.create_drafts(req)
    await db.commit()
    return result


@router.post("/messages", response_model=ChatEndMessageResponse, summary="초안을 기반으로 종료 채팅 메시지 생성")
async def create_message(
    req: ChatEndMessageRequest,
    service: ChatEndService = Depends(get_service),
    db: AsyncSession = Depends(get_db),
) -> ChatEndMessageResponse:
    result = await service.create_message(req)
    await db.commit()
    return result
