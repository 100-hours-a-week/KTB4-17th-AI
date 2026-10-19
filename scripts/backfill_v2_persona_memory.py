"""과거 대화 → v2 메모리 백필.

실행: uv run python -m scripts.backfill_v2_persona_memory [--user-id U] [--skip-onboarding] [--skip-practice]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.db import SessionLocal
from app.features.persona.models import OnboardingSession, OnboardingTurn
from app.features.practice.models import PracticeMessage
from app.v2.persona.agents import PersonaMemoryExtractor, PersonaMemoryReconcileJudge
from app.v2.persona.embedder import get_embedder
from app.v2.persona.graph import create_memory_graph
from app.v2.persona.models import UserPersonaFact
from app.v2.persona.pairs import build_pairs_from_onboarding_turns
from app.v2.persona.repository import PersonaMemoryRepository
from app.v2.persona.service import run_user_memory_update
from app.v2.persona.trigger import unprocessed_user_message_count

logger = logging.getLogger(__name__)
MAX_ROUNDS = 100  # 사용자 한 명당 연습대화 반복 상한 (무한 루프 방지)


async def backfill_onboarding(user_id: str) -> int:
    """온보딩 답변으로 코어/Fact 를 만든다. 이미 onboarding 출처 Fact 가 있으면 건너뛴다. 처리한 쌍 수를 반환."""
    async with SessionLocal() as db:
        done = await db.execute(
            select(UserPersonaFact.id)
            .where(UserPersonaFact.user_id == user_id, UserPersonaFact.source_type == "onboarding")
            .limit(1)
        )
        if done.scalar_one_or_none() is not None:
            return 0
        stmt = (
            select(OnboardingTurn)
            .join(OnboardingSession, OnboardingSession.id == OnboardingTurn.session_id)
            .where(OnboardingSession.user_id == user_id)
            .order_by(OnboardingTurn.id)
        )
        pairs = build_pairs_from_onboarding_turns(list((await db.execute(stmt)).scalars()))
        if not pairs:
            return 0

        repo = PersonaMemoryRepository(db)
        core = await repo.get_or_create_core(user_id)
        graph = create_memory_graph(
            repo=repo,
            embedder=get_embedder(),
            extractor=PersonaMemoryExtractor(),
            judge=PersonaMemoryReconcileJudge(),
        )
        await graph.ainvoke(
            {
                "user_id": user_id,
                "current_date": datetime.now(UTC).strftime("%Y-%m-%d"),
                "source_type": "onboarding",  # 커서 전진 금지 (turn id 는 practice_messages id 와 다른 번호 공간)
                "dialogue_pairs": pairs,
                "latest_message_id": max(p["message_id"] for p in pairs),
                "existing_core": dict(core.profile or {}),
            }
        )
        await db.commit()
        return len(pairs)


async def backfill_practice(user_id: str) -> int:
    """연습대화 미처리 발화가 없어질 때까지 반복 처리한다. 실행 횟수를 반환."""
    rounds = 0
    while rounds < MAX_ROUNDS:
        async with SessionLocal() as db:
            if await unprocessed_user_message_count(db, user_id) == 0:
                break
        if not await run_user_memory_update(user_id, "backfill"):
            break  # 다른 작업이 진행 중이거나 실패 — 다음 실행에서 이어서 한다
        rounds += 1
    return rounds


async def main(args: argparse.Namespace) -> None:
    if args.user_id:
        user_ids = [args.user_id]
    else:
        async with SessionLocal() as db:
            rows = await db.execute(
                select(PracticeMessage.user_id).where(PracticeMessage.user_id.is_not(None)).distinct()
            )
            user_ids = [u for (u,) in rows.all()]
    for uid in user_ids:
        ob = 0 if args.skip_onboarding else await backfill_onboarding(uid)
        pr = 0 if args.skip_practice else await backfill_practice(uid)
        print(f"user={uid} onboarding_pairs={ob} practice_rounds={pr}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id")
    parser.add_argument("--skip-onboarding", action="store_true")
    parser.add_argument("--skip-practice", action="store_true")
    asyncio.run(main(parser.parse_args()))
