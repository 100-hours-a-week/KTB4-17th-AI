"""DB 테이블.

대화 원문을 보관하는 이유: 추출이 실패하면 재시도해야 하고,
루브릭을 고친 뒤 과거 대화로 재추출해서 품질을 비교해야 한다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class OnboardingSession(Base):
    __tablename__ = "onboarding_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    nickname: Mapped[str] = mapped_column(String(20))
    total_turns: Mapped[int] = mapped_column(Integer, default=10)
    turn_index: Mapped[int] = mapped_column(Integer, default=0)

    # 지금 질문해두고 답변을 기다리는 주제의 id
    pending_topic_id: Mapped[str | None] = mapped_column(String(32))
    used_topic_ids: Mapped[list] = mapped_column(JSON, default=list)

    # {"primary": {차원: 건수}, "secondary": {...}}
    coverage: Mapped[dict] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(16), default="active")
    # active | completed | abandoned

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    turns: Mapped[list[ConversationTurn]] = relationship(
        back_populates="session",
        order_by="ConversationTurn.turn_index",
        cascade="all, delete-orphan",
    )


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("onboarding_sessions.id", ondelete="CASCADE"), index=True)
    turn_index: Mapped[int] = mapped_column(Integer)

    topic_id: Mapped[str] = mapped_column(String(32))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    skipped: Mapped[bool] = mapped_column(Boolean, default=False)  # 사용자가 건너뛴 질문

    # "llm" | "seed" — 폴백 빈도를 나중에 세기 위해 남긴다
    question_source: Mapped[str] = mapped_column(String(8), default="llm")

    tags: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    session: Mapped[OnboardingSession] = relationship(back_populates="turns")


class PersonaRecord(Base):
    __tablename__ = "personas"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("onboarding_sessions.id"), index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)

    scores: Mapped[dict] = mapped_column(JSON)
    texts: Mapped[dict] = mapped_column(JSON)  # interests/routine/date_*
    confidence: Mapped[dict] = mapped_column(JSON)  # {차원: "LOW"}
    narrative: Mapped[dict | None] = mapped_column(JSON)  # headline/body/traits

    # 같은 세션에서 재빌드할 때마다 새 행. 이전 행을 가리켜 "뭐가 바뀌었나"를 계산한다.
    version: Mapped[int] = mapped_column(Integer, default=1)
    previous_id: Mapped[str | None] = mapped_column(String(32))

    # 확인 루프 — "이대로 좋아요" 시각 / "다른 것 같아요" 면 어느 영역이 달랐는지
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    feedback: Mapped[dict | None] = mapped_column(JSON)  # {"agree": bool, "area": str | None}

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
