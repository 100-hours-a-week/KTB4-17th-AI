from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

QUESTION = "내 페르소나가 다른 유저에게 한 말이에요. 나다운 대답인가요?"


class FeedbackCard(Base):
    """개인정보 검토를 마친 후보. READY → SHOWN → ANSWERED 또는 EXPIRED."""

    __tablename__ = "feedback_card_candidates"
    __table_args__ = (
        CheckConstraint("status IN ('READY', 'SHOWN', 'ANSWERED', 'EXPIRED')", name="ck_feedback_card_status"),
        Index("ix_feedback_card_user_status", "user_id", "status"),
        Index(
            "uq_feedback_card_user_shown",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'SHOWN'"),
            sqlite_where=text("status = 'SHOWN'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"fc_{uuid.uuid4().hex}")
    user_id: Mapped[str] = mapped_column(String(64))
    context_question: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utterance: Mapped[str] = mapped_column(String(300))
    question: Mapped[str] = mapped_column(String(200), default=QUESTION)
    status: Mapped[str] = mapped_column(String(16), default="READY", server_default="READY")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    shown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FeedbackCardAnswer(Base):
    __tablename__ = "feedback_card_answers"
    __table_args__ = (
        CheckConstraint("answer IN ('YES', 'NO')", name="ck_feedback_answer_value"),
        CheckConstraint("answer = 'NO' OR correction_text IS NULL", name="ck_feedback_answer_correction"),
    )

    card_id: Mapped[str] = mapped_column(ForeignKey("feedback_card_candidates.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64))
    answer: Mapped[str] = mapped_column(String(3))
    correction_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consent_version: Mapped[str] = mapped_column(String(64))
