"""v2 연습대화 말 놓기 상태 — 세션마다 하나. 사용자 단위 거절은 user_persona_core.casual_speech_declined_at 에 둔다."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _now() -> datetime:
    return datetime.now(UTC)


# formal(아직 안 물음) → asked(제안함, 답 대기) → casual(허락, 반말) | declined(거절, 다시 안 물음)
class V2PracticeSpeechState(Base):
    __tablename__ = "v2_practice_speech_state"

    session_id: Mapped[str] = mapped_column(ForeignKey("practice_sessions.id", ondelete="CASCADE"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(12), default="formal")
    asked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
