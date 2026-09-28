"""DB 테이블 — 시뮬레이션 한 건.

대본(transcript)과 리포트(report)를 JSON 으로 통째로 둔다. 둘 다 "그때 그 페르소나 버전으로 나온 결과"라
페르소나가 재빌드돼도 바뀌면 안 되고, 다시 계산할 일도 없다 — 조회는 그대로 꺼내 주기만 한다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
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


class ReportPreviewRecord(Base):
    """`/report/preview` 호출 기록 — 내부 확인용. `simulations`과 달리 personas FK가 없다:

    요청 본문의 persona_a/b는 호출자가 직접 넣는 임의 JSON이라 실제 저장된 페르소나가
    아닐 수 있다(테스트/미리보기 전용이라는 설계 그대로). 항상 저장된다."""

    __tablename__ = "report_previews"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)

    persona_a: Mapped[dict] = mapped_column(JSON)  # 요청받은 PersonaResponse 그대로
    persona_b: Mapped[dict] = mapped_column(JSON)
    nickname_a: Mapped[str] = mapped_column(String(64))
    nickname_b: Mapped[str] = mapped_column(String(64))

    transcript: Mapped[list] = mapped_column(JSON, default=list)
    use_llm: Mapped[bool] = mapped_column(Boolean, default=True)

    report: Mapped[dict] = mapped_column(JSON)  # MatchingReport JSON
    narrative_source: Mapped[str] = mapped_column(String(16))  # llm | template

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
