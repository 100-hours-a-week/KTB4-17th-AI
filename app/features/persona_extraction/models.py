"""DB 테이블 — 페르소나 추출 작업과 업로드 대화의 본인 발화.

작업(job)을 남기는 이유: 추출은 LLM 호출이라 10~30초 걸린다. 요청은 job_id 만 돌려주고,
프론트가 상태를 조회한다. 같은 사용자의 작업은 동시에 하나만 — 둘 다 같은 페르소나에 버전을 쌓기 때문이다.

업로드 대화는 본인 발화만 저장한다 (상대방 발화·개인정보는 버린다 — 2026-10-06 결정).
저장해 두는 이유: 같은 파일을 다시 올리면 이미 본 발화를 건너뛰고, 추출 규칙을 고친 뒤 다시 돌려 볼 수 있다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

ACTIVE_STATUSES = ("pending", "running")


# PK 로 쓸 32자리 hex uuid 생성
def _uuid() -> str:
    return uuid.uuid4().hex


# 타임존 포함 현재 시각
def _now() -> datetime:
    return datetime.now(UTC)


# 추출 작업 한 건 — 연습대화(practice) 또는 업로드 대화(conversation)
class ExtractionJob(Base):
    __tablename__ = "extraction_jobs"
    # 사용자당 진행 중(pending|running) 작업은 하나. 확인과 생성 사이의 경합도 DB 가 막는다
    __table_args__ = (
        Index(
            "uq_extraction_jobs_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
            sqlite_where=text("status IN ('pending', 'running')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # practice | conversation
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | running | succeeded | failed
    persona_id: Mapped[str | None] = mapped_column(String(32))  # 성공하면 새로 만든 페르소나 버전
    analyzed_count: Mapped[int] = mapped_column(Integer, default=0)  # LLM 에 넘긴 발화 수
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# 업로드 대화에서 꺼낸 본인 발화 한 건
class ImportedUtterance(Base):
    __tablename__ = "imported_utterances"
    # 같은 사람이 같은 분에 같은 말을 한 건 순번(occurrence)으로 구분한다 — 같은 파일을 다시 올리면 같은 키가 나와 건너뛴다
    __table_args__ = (
        UniqueConstraint("user_id", "sent_at", "content_hash", "occurrence", name="uq_imported_utterances_identity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(8), default="kakao")
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # UTC 로 저장 (비교 키라서)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))  # sha256 hex — Text 컬럼에 유니크를 걸지 않으려고
    occurrence: Mapped[int] = mapped_column(Integer, default=0)
    job_id: Mapped[str] = mapped_column(String(32), index=True)  # 이 발화를 가져온 업로드 작업
    # 반영된 페르소나 버전. 업로드 작업은 새 발화를 전부(분석 안 한 오래된 것 포함) 반영 처리한다
    reflected_persona_id: Mapped[str | None] = mapped_column(String(32))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
