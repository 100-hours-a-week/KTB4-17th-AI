"""DB 테이블 — 시뮬레이션 한 건.

대본(transcript)과 리포트(report)를 JSON 으로 통째로 둔다. 둘 다 "그때 그 페르소나 버전으로 나온 결과"라
페르소나가 재빌드돼도 바뀌면 안 되고, 다시 계산할 일도 없다 — 조회는 그대로 꺼내 주기만 한다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class SimulationRecord(Base):
    __tablename__ = "simulations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)

    # a = 요청한 사용자(나), b = 상대. 페르소나는 특정 버전 행을 가리킨다.
    persona_a_id: Mapped[str] = mapped_column(ForeignKey("personas.id"), index=True)
    persona_b_id: Mapped[str] = mapped_column(ForeignKey("personas.id"), index=True)
    user_id_a: Mapped[str] = mapped_column(String(64), index=True)
    user_id_b: Mapped[str] = mapped_column(String(64), index=True)
    nickname_a: Mapped[str] = mapped_column(String(20))
    nickname_b: Mapped[str] = mapped_column(String(20))

    turns: Mapped[int] = mapped_column(Integer, default=10)  # 요청한 왕복 수
    transcript: Mapped[list] = mapped_column(JSON, default=list)  # [{index, speaker, text}]
    report: Mapped[dict] = mapped_column(JSON, default=dict)  # MatchingReport JSON
    narrative_source: Mapped[str] = mapped_column(String(8), default="llm")  # llm | template

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
