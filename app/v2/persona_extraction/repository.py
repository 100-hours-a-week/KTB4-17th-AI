"""DB 접근. service.py 는 SQLAlchemy 를 직접 만지지 않는다."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ACTIVE_STATUSES, V2ExtractionJob, V2ImportedUtterance

Identity = tuple[datetime, str, int]  # (sent_at UTC, content_hash, occurrence)


# SQLite 는 timezone 을 버리고 돌려준다 — 비교가 되게 UTC 로 다시 붙인다 (Postgres 는 그대로)
def _utc(at: datetime) -> datetime:
    return at.astimezone(UTC) if at.tzinfo else at.replace(tzinfo=UTC)


class ExtractionRepository:
    # 이 요청의 DB 세션을 보관
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── 작업 ──────────────────────────────────────────────

    # 작업 행을 만들어 flush 한다. 진행 중 작업이 이미 있으면 유니크 인덱스가 IntegrityError 를 낸다
    async def create_job(self, user_id: str, kind: str) -> V2ExtractionJob:
        job = V2ExtractionJob(user_id=user_id, kind=kind, status="pending")
        self.db.add(job)
        await self.db.flush()
        return job

    # id 로 작업 조회
    async def get_job(self, job_id: str) -> V2ExtractionJob | None:
        return await self.db.get(V2ExtractionJob, job_id)

    # 그 사용자의 진행 중(pending|running) 작업
    async def active_job(self, user_id: str) -> V2ExtractionJob | None:
        stmt = select(V2ExtractionJob).where(
            V2ExtractionJob.user_id == user_id, V2ExtractionJob.status.in_(ACTIVE_STATUSES)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    # ── 업로드 발화 ───────────────────────────────────────

    # 이미 저장된 발화 키 중 주어진 것과 겹치는 것. 후보를 해시로 좁혀 읽고 키 비교는 파이썬에서 한다
    # (DB 마다 timezone 표현이 달라 tuple IN 비교가 어긋나는 것을 피한다)
    async def existing_identities(self, user_id: str, identities: list[Identity]) -> set[Identity]:
        if not identities:
            return set()
        hashes = {h for _, h, _ in identities}
        stmt = select(
            V2ImportedUtterance.sent_at, V2ImportedUtterance.content_hash, V2ImportedUtterance.occurrence
        ).where(V2ImportedUtterance.user_id == user_id, V2ImportedUtterance.content_hash.in_(hashes))
        stored = {(_utc(at), h, o) for at, h, o in (await self.db.execute(stmt)).all()}
        return stored & set(identities)

    # 업로드 발화 저장
    async def add_utterances(self, utterances: list[V2ImportedUtterance]) -> None:
        self.db.add_all(utterances)
        await self.db.flush()

    # 이 업로드 작업이 저장한 발화 전부, 오래된 순
    async def job_utterances(self, job_id: str) -> list[V2ImportedUtterance]:
        stmt = (
            select(V2ImportedUtterance)
            .where(V2ImportedUtterance.job_id == job_id)
            .order_by(V2ImportedUtterance.sent_at, V2ImportedUtterance.occurrence, V2ImportedUtterance.id)
        )
        return list((await self.db.execute(stmt)).scalars())

    # 반영한 업로드 발화에 페르소나 버전을 기록한다
    async def mark_imported_reflected(self, utterances: list[V2ImportedUtterance], persona_id: str) -> None:
        for u in utterances:
            u.reflected_persona_id = persona_id
        await self.db.flush()
