"""v2 페르소나 메모리 조회 · 수동 동기화 · TTL 정리 API."""

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import require_internal_key

from .models import UserPersonaFact
from .repository import PersonaMemoryRepository
from .schemas import FactItemResponse, UserMemoryResponse
from .service import cleanup_expired_tentative_facts, run_user_memory_update

router = APIRouter(
    prefix="/v2/persona",
    tags=["v2_persona"],
    dependencies=[Depends(require_internal_key)],
)


@router.get("/memory/{user_id}", response_model=UserMemoryResponse)
async def get_user_memory(
    user_id: str,
    db: AsyncSession = Depends(get_db),
) -> UserMemoryResponse:
    repo = PersonaMemoryRepository(db)
    core = await repo.get_core(user_id)
    stmt = select(UserPersonaFact).where(UserPersonaFact.user_id == user_id).order_by(UserPersonaFact.updated_at.desc())
    facts = list((await db.execute(stmt)).scalars())
    return UserMemoryResponse(
        user_id=user_id,
        core_profile=dict(core.profile) if core and core.profile else {},
        last_processed_message_id=core.last_processed_message_id if core else 0,
        facts=[
            FactItemResponse(
                id=f.id,
                category=f.category,
                fact=f.fact,
                confidence=f.confidence,
                status=f.status,
                updated_at=f.updated_at,
            )
            for f in facts
        ],
    )


@router.post("/memory/{user_id}/sync")
async def trigger_user_memory_sync(
    user_id: str,
    background: BackgroundTasks,
) -> dict[str, str]:
    background.add_task(run_user_memory_update, user_id, "manual")
    return {"status": "scheduled", "user_id": user_id}


@router.post("/memory/cleanup-tentative")
async def run_tentative_ttl_cleanup(
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    deleted = await cleanup_expired_tentative_facts(db)
    return {"deleted_count": deleted}
