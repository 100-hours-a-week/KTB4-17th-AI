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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


JSONBCompat = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class OnboardingSession(Base):
    __tablename__ = "onboarding_sessions"
    __table_args__ = (
        CheckConstraint(
            "mbti IS NULL OR mbti IN ('ENFJ', 'ENFP', 'ENTJ', 'ENTP', 'ESFJ', 'ESFP', 'ESTJ', 'ESTP', "
            "'INFJ', 'INFP', 'INTJ', 'INTP', 'ISFJ', 'ISFP', 'ISTJ', 'ISTP')",
            name="ck_onboarding_sessions_mbti_valid",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    nickname: Mapped[str] = mapped_column(String(20))
    total_turns: Mapped[int] = mapped_column(Integer, default=10)
    turn_index: Mapped[int] = mapped_column(Integer, default=0)
    # 시작 요청에서 받은 MBTI. /confirm 이 MBTI 없이 와도 확정된 페르소나에 이 값을 옮겨 적는다
    mbti: Mapped[str | None] = mapped_column(String(4))

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
    summaries: Mapped[list | None] = mapped_column(JSON)  # area별 [{category, title, content}]
    # 실제 대화(연습대화·카카오톡)에서 뽑은 말투·대화 습관 (schemas.ConversationStyle).
    # 온보딩 빌드 버전은 None — 온보딩으로 재빌드하면 추출 반영분을 초기화하기로 했다 (2026-10-05)
    conversation_style: Mapped[dict | None] = mapped_column(JSON)

    # 같은 세션에서 재빌드할 때마다 새 행. 이전 행을 가리켜 "뭐가 바뀌었나"를 계산한다.
    version: Mapped[int] = mapped_column(Integer, default=1)
    previous_id: Mapped[str | None] = mapped_column(String(32))

    # /build 에서는 미확정 초안으로 만들고, /confirm 에서 사용자 승인을 반영한다.
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mbti: Mapped[str | None] = mapped_column(String(4))
    # "llm" | "fallback" | "practice" | "kakao" | "reset" — 추출 또는 리셋 버전 포함
    source: Mapped[str] = mapped_column(String(8), default="llm", server_default="llm")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PersonaCardRecord(Base):
    """Kanana-2 SFT 및 캐릭터 대화용 구조화된 Persona Card ORM 모델."""

    __tablename__ = "persona_cards"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    persona_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(50))
    gender: Mapped[str] = mapped_column(String(16), default="unspecified")  # female | male | unspecified
    generation_mode: Mapped[str] = mapped_column(String(32))  # self_mirror | compatible_character | fictional_variant
    mbti_reference: Mapped[str | None] = mapped_column(String(4))

    # 15개 지표 세부 객체 (raw_score, effective_score, delta, level, status, interpretation)
    traits: Mapped[dict] = mapped_column(JSONBCompat, nullable=False)

    # 결측치 관리 배열
    imputed_scores: Mapped[list] = mapped_column(JSONBCompat, default=list)
    unknown_origin_scores: Mapped[list] = mapped_column(JSONBCompat, default=list)
    available_summaries: Mapped[list] = mapped_column(JSONBCompat, default=list)
    missing_summaries: Mapped[list] = mapped_column(JSONBCompat, default=list)

    # 행동 및 선호 지표 분류
    behavior_traits: Mapped[dict] = mapped_column(JSONBCompat, nullable=False)
    partner_preferences: Mapped[dict] = mapped_column(JSONBCompat, nullable=False)

    # 대화 및 관계 스타일
    communication_style: Mapped[dict] = mapped_column(JSONBCompat, nullable=False)
    conflict_style: Mapped[dict] = mapped_column(JSONBCompat, nullable=False)
    relationship_style: Mapped[dict] = mapped_column(JSONBCompat, nullable=False)

    # 근거 기반 행동 규칙
    personality_rules: Mapped[list] = mapped_column(JSONBCompat, nullable=False)

    # 동적 대화 상태 (current_mood, closeness, trust, current_context)
    dynamic_state: Mapped[dict] = mapped_column(JSONBCompat, nullable=False)

    # Kanana-2 SFT용 멀티턴 대화 예시
    dialogue_examples: Mapped[list] = mapped_column(JSONBCompat, default=list)

    # 메타데이터 및 검증
    evidence: Mapped[list] = mapped_column(JSONBCompat, default=list)
    creative_elements: Mapped[list] = mapped_column(JSONBCompat, default=list)
    conflicts: Mapped[list] = mapped_column(JSONBCompat, default=list)
    quality_checks: Mapped[dict] = mapped_column(JSONBCompat, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
