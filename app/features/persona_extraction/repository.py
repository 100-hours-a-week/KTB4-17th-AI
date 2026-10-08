"""DB 접근. service.py 는 SQLAlchemy 를 직접 만지지 않는다.

연습대화 발화(practice_messages)도 여기서 읽고 표시한다 — 추출 쪽 관심사라서.
simulation 이 personas 를 읽는 것처럼 기능 간 모델 import 는 허용한다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.practice.models import PracticeMessage, PracticeSession

from .models import ACTIVE_STATUSES, ExtractionJob, ImportedUtterance

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
    async def create_job(self, user_id: str, kind: str) -> ExtractionJob:
        job = ExtractionJob(user_id=user_id, kind=kind, status="pending")
        self.db.add(job)
        await self.db.flush()
        return job

    # id 로 작업 조회
    async def get_job(self, job_id: str) -> ExtractionJob | None:
        return await self.db.get(ExtractionJob, job_id)

    # 그 사용자의 진행 중(pending|running) 작업
    async def active_job(self, user_id: str) -> ExtractionJob | None:
        stmt = select(ExtractionJob).where(ExtractionJob.user_id == user_id, ExtractionJob.status.in_(ACTIVE_STATUSES))
        return (await self.db.execute(stmt)).scalar_one_or_none()

    # ── 연습대화 발화 ──────────────────────────────────────

    # 아직 반영하지 않은 내 발화(role=user) 전부, 오래된 순
    async def unreflected_practice_messages(self, user_id: str) -> list[PracticeMessage]:
        stmt = (
            select(PracticeMessage)
            .where(
                PracticeMessage.user_id == user_id,
                PracticeMessage.role == "user",
                PracticeMessage.reflected_persona_id.is_(None),
            )
            .order_by(PracticeMessage.created_at, PracticeMessage.id)
        )
        return list((await self.db.execute(stmt)).scalars())

    # 반영한 발화에 페르소나 버전을 기록한다. 표시가 있으면 다시 쓰지 않는다 (온보딩 재빌드 뒤에도)
    async def mark_practice_reflected(self, messages: list[PracticeMessage], persona_id: str) -> None:
        for m in messages:
            m.reflected_persona_id = persona_id
        await self.db.flush()

    # 이 사용자의 연습대화에 나온 닉네임(내 것 + 상대 것) — 자주 쓰는 말에서 이름을 거르는 데 쓴다
    async def practice_nicknames(self, user_id: str) -> set[str]:
        stmt = select(PracticeSession.my_nickname, PracticeSession.partner_nickname).where(
            PracticeSession.user_id == user_id
        )
        return {name for row in (await self.db.execute(stmt)).all() for name in row if name}

    # ── 업로드 발화 ───────────────────────────────────────

    # 이미 저장된 발화 키 중 주어진 것과 겹치는 것. 후보를 해시로 좁혀 읽고 키 비교는 파이썬에서 한다
    # (DB 마다 timezone 표현이 달라 tuple IN 비교가 어긋나는 것을 피한다)
    async def existing_identities(self, user_id: str, identities: list[Identity]) -> set[Identity]:
        if not identities:
            return set()
        hashes = {h for _, h, _ in identities}
        stmt = select(ImportedUtterance.sent_at, ImportedUtterance.content_hash, ImportedUtterance.occurrence).where(
            ImportedUtterance.user_id == user_id, ImportedUtterance.content_hash.in_(hashes)
        )
        stored = {(_utc(at), h, o) for at, h, o in (await self.db.execute(stmt)).all()}
        return stored & set(identities)

    # 업로드 발화 저장
    async def add_utterances(self, utterances: list[ImportedUtterance]) -> None:
        self.db.add_all(utterances)
        await self.db.flush()

    # 이 업로드 작업이 저장한 발화 전부, 오래된 순
    async def job_utterances(self, job_id: str) -> list[ImportedUtterance]:
        stmt = (
            select(ImportedUtterance)
            .where(ImportedUtterance.job_id == job_id)
            .order_by(ImportedUtterance.sent_at, ImportedUtterance.occurrence, ImportedUtterance.id)
        )
        return list((await self.db.execute(stmt)).scalars())

    # 반영한 업로드 발화에 페르소나 버전을 기록한다
    async def mark_imported_reflected(self, utterances: list[ImportedUtterance], persona_id: str) -> None:
        for u in utterances:
            u.reflected_persona_id = persona_id
        await self.db.flush()
