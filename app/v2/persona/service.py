"""v2 메모리 백그라운드 워커 서비스 (Stale 회수 + 스냅샷 커서 + 20분 무응답 스위퍼 + TTL 정리 + 재임베딩)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.features.practice.models import PracticeMessage

from .agents import PersonaMemoryExtractor, PersonaMemoryReconcileJudge
from .embedder import EmbedderProtocol, get_embedder
from .graph import create_memory_graph
from .models import MemoryUpdateJob, UserPersonaCore, UserPersonaFact
from .pairs import build_pairs_from_practice_messages
from .repository import PersonaMemoryRepository
from .trigger import should_trigger_memory_update

logger = logging.getLogger(__name__)

# 한 번의 실행에서 LLM 에 넘기는 사용자 발화 상한. 남은 것은 다음 트리거/스위퍼/백필 반복이 이어서 처리한다
MAX_USER_MSGS_PER_RUN = 40


async def reclaim_stale_memory_jobs(db: AsyncSession, user_id: str | None = None) -> int:
    """서버 재시작 등으로 멈춘(10분 초과) pending/running 작업을 failed(stale)로 회수한다."""
    cutoff = datetime.now(UTC) - timedelta(minutes=get_settings().memory_job_stale_minutes)
    stmt = select(MemoryUpdateJob).where(MemoryUpdateJob.status.in_(("pending", "running")))
    if user_id is not None:
        stmt = stmt.where(MemoryUpdateJob.user_id == user_id)

    rows = list((await db.execute(stmt)).scalars())
    now = datetime.now(UTC)
    reclaimed = 0
    for job in rows:
        since = job.started_at or job.created_at
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        if since <= cutoff:
            job.status = "failed"
            job.error = "stale: timeout exceeded"
            job.finished_at = now
            reclaimed += 1
    if reclaimed:
        await db.flush()
    return reclaimed


async def try_create_memory_job(db: AsyncSession, user_id: str, reason: str) -> MemoryUpdateJob | None:
    """Stale 작업을 먼저 회수한 뒤 새 pending 작업을 만든다. 이미 활성 작업이 있으면 None 반환."""
    await reclaim_stale_memory_jobs(db, user_id=user_id)
    job = MemoryUpdateJob(user_id=user_id, trigger_reason=reason, status="pending")
    try:
        async with db.begin_nested():
            db.add(job)
            await db.flush()
        return job
    except IntegrityError:
        return None


async def run_user_memory_update(user_id: str, reason: str = "turn_count") -> bool:
    """BackgroundTasks 또는 arq 워커에서 호출하는 사용자 단위 직렬 메모리 업데이트 엔트리포인트."""
    settings = get_settings()
    async with SessionLocal() as db:
        job = await try_create_memory_job(db, user_id, reason)
        if job is None:
            return False
        job.status = "running"
        job.started_at = datetime.now(UTC)
        await db.commit()

        try:
            repo = PersonaMemoryRepository(db)
            core = await repo.get_or_create_core(user_id)
            last_id = core.last_processed_message_id

            # 스냅샷 조회: 현재 시점까지의 세션 메시지들을 가져와 직전 2쌍 오버랩 포함 조립
            msg_stmt = (
                select(PracticeMessage).where(PracticeMessage.user_id == user_id).order_by(PracticeMessage.id.asc())
            )
            all_msgs = list((await db.execute(msg_stmt)).scalars())
            new_user_msgs = [m for m in all_msgs if m.role == "user" and m.id > last_id]
            if not new_user_msgs:
                job.status = "succeeded"
                job.finished_at = datetime.now(UTC)
                await db.commit()
                return True

            batch = new_user_msgs[:MAX_USER_MSGS_PER_RUN]  # id 오름차순
            snapshot_max_id = batch[-1].id
            pairs = build_pairs_from_practice_messages(
                [m for m in all_msgs if m.id <= snapshot_max_id],
                last_processed_id=last_id,
                overlap=settings.memory_context_overlap_pairs,
            )

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
                    "source_type": "practice",
                    "dialogue_pairs": pairs,
                    "latest_message_id": snapshot_max_id,
                    "existing_core": dict(core.profile or {}),
                }
            )
            job.status = "succeeded"
            job.finished_at = datetime.now(UTC)
            await db.commit()
            return True
        except Exception as exc:
            await db.rollback()
            job_row = await db.get(MemoryUpdateJob, job.id)
            if job_row is not None:
                job_row.status = "failed"
                job_row.error = str(exc)[:500]
                job_row.finished_at = datetime.now(UTC)
                await db.commit()
            logger.exception("v2 memory update failed for user=%s", user_id)
            return False


async def sweep_idle_users_for_memory_update(db: AsyncSession) -> list[str]:
    """마지막 활동 후 20분 이상 경과하고 미처리 메시지가 남아 있는 사용자 목록을 찾아 업데이트를 트리거한다."""
    cutoff = datetime.now(UTC) - timedelta(minutes=get_settings().memory_trigger_idle_minutes)
    stmt = select(UserPersonaCore).where(
        UserPersonaCore.last_activity_at.is_not(None),
        UserPersonaCore.last_activity_at <= cutoff,
    )
    cores = list((await db.execute(stmt)).scalars())
    triggered_users: list[str] = []
    for core in cores:
        msg_stmt = (
            select(PracticeMessage.id)
            .where(
                PracticeMessage.user_id == core.user_id,
                PracticeMessage.role == "user",
                PracticeMessage.id > core.last_processed_message_id,
            )
            .limit(1)
        )
        has_unprocessed = (await db.execute(msg_stmt)).scalar_one_or_none()
        if has_unprocessed is not None:
            triggered_users.append(core.user_id)
    return triggered_users


async def cleanup_expired_tentative_facts(db: AsyncSession, *, ttl_days: int | None = None) -> int:
    """생성된 지 30일(memory_tentative_ttl_days)이 경과한 tentative 행을 삭제한다."""
    days = ttl_days if ttl_days is not None else get_settings().memory_tentative_ttl_days
    cutoff = datetime.now(UTC) - timedelta(days=days)
    stmt = delete(UserPersonaFact).where(
        UserPersonaFact.status == "tentative",
        UserPersonaFact.created_at < cutoff,
    )
    result = await db.execute(stmt)
    await db.commit()
    return int(result.rowcount or 0)


async def reembed_outdated_facts(
    db: AsyncSession,
    embedder: EmbedderProtocol,
    *,
    target_model_name: str | None = None,
    batch_size: int = 64,
) -> int:
    """모델 교체 시 embedding_model 버전이 다른 Fact 행들을 단계적으로 재임베딩한다 (계획서 5.5, 9절)."""
    model_name = target_model_name or get_settings().embedding_model_name
    stmt = (
        select(UserPersonaFact)
        .where((UserPersonaFact.embedding_model.is_(None)) | (UserPersonaFact.embedding_model != model_name))
        .limit(batch_size)
    )
    rows = list((await db.execute(stmt)).scalars())
    if not rows:
        return 0
    vecs = await embedder.encode_batch([r.fact for r in rows])
    for row, vec in zip(rows, vecs, strict=False):
        row.embedding = vec
        row.embedding_model = model_name
    await db.commit()
    return len(rows)


async def maybe_run_memory_update(user_id: str, latest_user_message: str) -> None:
    """내 메시지에 답한 뒤(응답 전송 후 백그라운드) 호출한다. 활동 시각을 남기고, 트리거 조건이 맞으면 업데이트를 돈다.

    새 DB 세션을 연다 (요청 세션은 응답과 함께 닫힌다). 실패해도 대화에는 영향이 없어야 하므로 예외를 삼키고 로그만 남긴다.
    """
    try:
        async with SessionLocal() as db:
            await PersonaMemoryRepository(db).touch_user_activity(user_id)
            await db.commit()
            should, reason = await should_trigger_memory_update(
                db, user_id=user_id, latest_user_message=latest_user_message
            )
        if should and reason:
            await run_user_memory_update(user_id, reason)
    except Exception:  # noqa: BLE001
        logger.exception("v2 memory trigger failed for user=%s", user_id)


async def idle_sweeper_loop(interval_s: float = 300.0) -> None:
    """20분 무응답 트리거. lifespan 에서 asyncio 태스크로 띄운다 (main.py)."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            async with SessionLocal() as db:
                users = await sweep_idle_users_for_memory_update(db)
            for uid in users:
                await run_user_memory_update(uid, "idle")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("v2 idle sweeper iteration failed")
