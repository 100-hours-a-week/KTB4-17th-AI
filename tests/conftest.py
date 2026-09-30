"""service 테스트 공용 — in-memory SQLite 에 진짜 repository 를 붙이고, LLM 만 가짜로 바꾼다."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

# 로컬 .env가 있어도 테스트 trace를 실제 Langfuse 프로젝트로 전송하지 않는다.
os.environ["LANGFUSE_TRACING_ENABLED"] = "false"

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.guardrail_trace  # noqa: F401
import app.features.practice.models  # noqa: F401  테이블을 metadata 에 등록
import app.features.simulation.models  # noqa: F401
from app.core.db import Base
from app.features.persona.models import OnboardingSession, PersonaRecord

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


async def with_db(scenario: Callable[[async_sessionmaker[AsyncSession]], Awaitable]):
    """빈 DB 를 만들어 세션 팩토리를 넘긴다. 요청 하나 = 세션 하나처럼 쓰라고 팩토리째 준다."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        return await scenario(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


async def seed_persona(
    db: AsyncSession,
    *,
    persona_id: str,
    user_id: str,
    nickname: str,
    confirmed: bool = True,
    scores: dict | None = None,
    texts: dict | None = None,
    confidence: dict | None = None,
    mbti: str | None = None,
    session_mbti: str | None = None,
) -> PersonaRecord:
    """온보딩이 끝나 페르소나가 저장된 상태를 만든다. confirmed=False 면 /confirm 전 초안."""
    session = OnboardingSession(
        id=f"s-{persona_id}",
        user_id=user_id,
        nickname=nickname,
        mbti=session_mbti,
        total_turns=10,
        turn_index=10,
        pending_topic_id=None,
        used_topic_ids=[],
        coverage={},
        status="completed",
        turns=[],
    )
    record = PersonaRecord(
        id=persona_id,
        session_id=session.id,
        user_id=user_id,
        scores=scores or {},
        texts=texts or {},
        confidence=confidence or {},
        narrative=None,
        version=1,
        is_confirmed=confirmed,
        confirmed_at=NOW if confirmed else None,
        mbti=mbti,
    )
    db.add_all([session, record])
    await db.commit()
    return record
