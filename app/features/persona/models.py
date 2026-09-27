"""DB 테이블.

대화 원문을 보관하는 이유: 추출이 실패하면 재시도해야 하고,
루브릭을 고친 뒤 과거 대화로 재추출해서 품질을 비교해야 한다.

Base 는 app.core.db 의 것을 쓴다 (simulation·practice 가 personas 에 FK 를 건다).
`from app.features.persona.models import Base` 는 그대로 동작한다 — 플레이그라운드 호환.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class OnboardingSession(Base):
    __tablename__ = "onboarding_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
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

    turns: Mapped[list[OnboardingTurn]] = relationship(
        back_populates="session",
        order_by="OnboardingTurn.turn_index",
        cascade="all, delete-orphan",
    )


class OnboardingTurn(Base):
    __tablename__ = "onboarding_turns"
    # 한 세션의 같은 턴에 질문 행은 하나뿐. 세션 잠금을 뚫은 동시 요청이 있어도 늦게 쓰는 쪽이 실패한다
    __table_args__ = (UniqueConstraint("session_id", "turn_index", name="uq_onboarding_turns_session_turn"),)

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
    __table_args__ = (
        UniqueConstraint("session_id", "version", name="uq_personas_session_version"),
        CheckConstraint(
            "(is_confirmed AND confirmed_at IS NOT NULL) OR (NOT is_confirmed AND confirmed_at IS NULL)",
            name="ck_personas_confirmation_consistent",
        ),
        CheckConstraint("mbti IS NULL OR is_confirmed", name="ck_personas_mbti_confirmed_only"),
        CheckConstraint(
            "mbti IS NULL OR mbti IN ('ENFJ', 'ENFP', 'ENTJ', 'ENTP', 'ESFJ', 'ESFP', 'ESTJ', 'ESTP', "
            "'INFJ', 'INFP', 'INTJ', 'INTP', 'ISFJ', 'ISFP', 'ISTJ', 'ISTP')",
            name="ck_personas_mbti_valid",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("onboarding_sessions.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)

    scores: Mapped[dict] = mapped_column(JSON)
    texts: Mapped[dict] = mapped_column(JSON)  # interests/routine/date_*
    confidence: Mapped[dict] = mapped_column(JSON)  # {차원: "LOW"}
    narrative: Mapped[dict | None] = mapped_column(JSON)  # headline/body/traits

    # 같은 세션에서 재빌드할 때마다 새 행. 이전 행을 가리켜 "뭐가 바뀌었나"를 계산한다.
    version: Mapped[int] = mapped_column(Integer, default=1)
    previous_id: Mapped[str | None] = mapped_column(String(32))

    # /build 에서는 미확정 초안으로 만들고, /confirm 에서 사용자 승인을 반영한다.
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mbti: Mapped[str | None] = mapped_column(String(4))
    # "llm" | "fallback" — 추출 LLM 이 실패해 규칙으로 만든 초안이면 fallback. 다음 /build 때 LLM 으로 다시 시도한다
    source: Mapped[str] = mapped_column(String(8), default="llm", server_default="llm")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
