"""DB 테이블 — 연습대화 세션과 메시지.

메시지를 전부 남기는 이유: 다음 답변의 맥락이 여기서 나온다 (LLM 은 상태가 없다).
새로고침해도 대화가 이어져야 하고, 나중에 "연습에서 뭘 어려워했나"를 볼 수도 있어야 한다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


# PK 로 쓸 32자리 hex uuid 생성
def _uuid() -> str:
    return uuid.uuid4().hex


# 타임존 포함 현재 시각 (created_at/updated_at 기본값)
def _now() -> datetime:
    return datetime.now(UTC)


# 연습대화 세션 한 건 — 상대(말하는 쪽)와 나(선택) 페르소나, 진행 상태를 담는다
class PracticeSession(Base):
    __tablename__ = "practice_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)

    # 상대 — 저장된 페르소나의 특정 버전. 이 페르소나가 "말하는 쪽"이다
    partner_persona_id: Mapped[str] = mapped_column(ForeignKey("personas.id"), index=True)
    partner_user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    partner_nickname: Mapped[str] = mapped_column(String(20))

    # 나 — 페르소나가 있으면 상대가 나를 "아는" 만큼만 참고한다. 없어도 된다
    my_persona_id: Mapped[str | None] = mapped_column(ForeignKey("personas.id"), index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    my_nickname: Mapped[str] = mapped_column(String(20))

    message_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | ended

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    messages: Mapped[list[PracticeMessage]] = relationship(
        back_populates="session",
        order_by="PracticeMessage.index",
        cascade="all, delete-orphan",
    )


# 세션에 속한 메시지 한 건 (유저 발화 또는 페르소나 답변)
class PracticeMessage(Base):
    __tablename__ = "practice_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("practice_sessions.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer)

    role: Mapped[str] = mapped_column(String(8))  # user | persona
    content: Mapped[str] = mapped_column(Text)
    # "llm" | "fallback" — 페르소나 메시지만. 폴백 빈도를 나중에 세기 위해
    source: Mapped[str | None] = mapped_column(String(8))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    session: Mapped[PracticeSession] = relationship(back_populates="messages")
