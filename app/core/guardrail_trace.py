"""Guardrail audit rows. A logging failure must never fail a user request."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.guardrail import ValidationResult

logger = logging.getLogger(__name__)


class GuardrailTrace(Base):
    __tablename__ = "guardrail_traces"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    feature: Mapped[str] = mapped_column(String(32))
    operation: Mapped[str] = mapped_column(String(32))
    session_id: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[str | None] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(16))
    guardrail_status: Mapped[str] = mapped_column(String(16))
    grade: Mapped[str] = mapped_column(String(16))
    initial_grade: Mapped[str] = mapped_column(String(16))
    violation_domains: Mapped[list[str]] = mapped_column(JSON)
    initial_response_text: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


async def record_guardrail(
    db: AsyncSession,
    *,
    feature: str,
    operation: str,
    session_id: str | None,
    user_id: str | None,
    mode: str,
    result: ValidationResult | None,
    initial_text: str,
) -> None:
    if mode == "off" or result is None:
        return
    try:
        keep_text = result.initial_grade in {"RETRYABLE", "BLOCK"} or result.status == "SHADOW_FAIL"
        async with db.begin_nested():
            db.add(
                GuardrailTrace(
                    feature=feature,
                    operation=operation,
                    session_id=session_id,
                    user_id=user_id,
                    mode=mode,
                    guardrail_status=result.status,
                    grade=result.grade,
                    initial_grade=result.initial_grade,
                    violation_domains=[v.domain for v in result.violations],
                    initial_response_text=initial_text[:2000] if keep_text else None,
                    retry_count=1 if result.regenerated else 0,
                    latency_ms=result.latency_ms,
                )
            )
            await db.flush()
    except Exception:
        logger.warning("guardrail trace recording failed", exc_info=True)
