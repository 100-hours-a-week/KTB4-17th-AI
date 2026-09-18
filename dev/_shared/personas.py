"""플레이그라운드 전용 — 저장된 페르소나 목록. 프로덕션 API 에는 없는 조회라 여기 둔다."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.persona.models import OnboardingSession, PersonaRecord
from app.features.persona.service import accuracy_of


async def list_personas(db: AsyncSession) -> list[dict]:
    """세션당 최신 버전만. 최근 것 먼저."""
    stmt = (
        select(PersonaRecord, OnboardingSession.nickname)
        .join(OnboardingSession, OnboardingSession.id == PersonaRecord.session_id)
        .order_by(PersonaRecord.created_at.desc())
    )
    seen: set[str] = set()
    out = []
    for record, nickname in (await db.execute(stmt)).all():
        if record.session_id in seen:
            continue
        seen.add(record.session_id)
        out.append(
            {
                "persona_id": record.id,
                "session_id": record.session_id,
                "user_id": record.user_id,
                "nickname": nickname,
                "version": record.version,
                "headline": (record.narrative or {}).get("headline"),
                "accuracy": accuracy_of(record.confidence),
                "created_at": record.created_at.isoformat(),
            }
        )
    return out
