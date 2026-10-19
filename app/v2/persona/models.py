"""v2 사용자 페르소나 메모리(코어 프로필 / 세부 사실 / 백그라운드 작업) ORM 모델."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


JSONBCompat = JSON().with_variant(JSONB(), "postgresql")
# 기준 타입이 Vector 여야 `UserPersonaFact.embedding.cosine_distance(...)` 가 된다. JSON 기준에 Vector 를
# variant 로 달면 Postgres 에서 AttributeError (실행 확인). SQLite(테스트)에서만 JSON 으로 저장한다.
VectorCompat = Vector(1024).with_variant(JSON(), "sqlite")
BigIntCompat = BigInteger().with_variant(Integer, "sqlite")


class UserPersonaCore(Base):
    __tablename__ = "user_persona_core"

    # 기존 personas, practice_sessions, practice_messages 와 호환되도록 String(64) 사용
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile: Mapped[dict] = mapped_column(JSONBCompat, default=dict, nullable=False)
    # practice_messages.id 기준 마지막으로 추출 처리한 메시지 커서
    last_processed_message_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 20분 무응답 트리거 판정을 위한 마지막 사용자 발화 시각
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 연습대화에서 "말 편하게 해도 될까요?"를 거절한 시각. 있으면 이 사용자에게는 다시 묻지 않는다
    casual_speech_declined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class UserPersonaFact(Base):
    __tablename__ = "user_persona_fact"
    __table_args__ = (
        # 유저당 Fact는 수백 건 이하이므로 B-Tree 복합 인덱스로 필터링 후 Exact Vector Scan 수행
        Index("idx_user_persona_fact_user_status", "user_id", "status"),
        Index("idx_user_persona_fact_user_category", "user_id", "category"),
    )

    id: Mapped[int] = mapped_column(BigIntCompat, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str | None] = mapped_column(String(32))  # 취미 / 가치관 / 일상 / 연애관 / 과거이력
    fact: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="confirmed", nullable=False)  # confirmed | tentative
    embedding: Mapped[list[float] | None] = mapped_column(VectorCompat)
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    source_type: Mapped[str | None] = mapped_column(String(16))  # practice | onboarding | core_demote
    source_message_id: Mapped[int | None] = mapped_column(BigIntCompat)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class MemoryUpdateJob(Base):
    __tablename__ = "memory_update_jobs"
    __table_args__ = (
        Index(
            "uq_memory_update_jobs_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
            sqlite_where=text("status IN ('pending', 'running')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    trigger_reason: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # turn_count | idle | signal | session_end | manual | backfill
    status: Mapped[str] = mapped_column(
        String(16), default="pending", nullable=False
    )  # pending | running | succeeded | failed
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
