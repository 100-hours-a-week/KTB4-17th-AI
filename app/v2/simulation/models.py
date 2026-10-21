"""DB 테이블 — v2 시뮬레이션 작업(job)과 결과.

작업을 따로 두는 이유: 하네스는 LLM 을 여러 번(최대 7회) 불러 수십 초 걸린다. 요청은 job_id 만 돌려주고
프론트가 상태를 폴링한다. 같은 (요청자, 상대) 조합의 작업은 동시에 하나만 — 더블클릭·재시도로 LLM 호출이 중복되지 않게.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.v2.persona.models import JSONBCompat

ACTIVE_STATUSES = ("pending", "running")


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class V2SimulationJob(Base):
    __tablename__ = "v2_simulation_jobs"
    __table_args__ = (
        # 같은 조합의 진행 중(pending|running) 작업은 하나. 확인과 생성 사이의 경합도 DB 가 막는다
        Index(
            "uq_v2_simulation_jobs_active_pair",
            "me_user_id",
            "partner_key",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
            sqlite_where=text("status IN ('pending', 'running')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    me_user_id: Mapped[str] = mapped_column(String(64), index=True)
    # 중복 판정 키. 상대 user_id (없으면 페르소나 id). 페르소나 버전이 바뀌어도(카카오 추출 등) 같은 상대로 본다
    partner_key: Mapped[str] = mapped_column(String(64))
    partner_persona_id: Mapped[str] = mapped_column(String(32))  # 요청 시점에 해석된 상대 페르소나 버전
    turns: Mapped[int] = mapped_column(Integer, default=10)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | running | succeeded | failed
    stage: Mapped[str | None] = mapped_column(String(16))  # topics | generating | verifying | repairing | reporting
    simulation_id: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)  # 실패 reason

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class V2Simulation(Base):
    __tablename__ = "v2_simulations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    me_user_id: Mapped[str] = mapped_column(String(64), index=True)
    partner_user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    persona_a_id: Mapped[str] = mapped_column(String(32))
    persona_b_id: Mapped[str] = mapped_column(String(32))
    nickname_a: Mapped[str] = mapped_column(String(20))
    nickname_b: Mapped[str] = mapped_column(String(20))
    turns: Mapped[int] = mapped_column(Integer, default=10)
    mode: Mapped[str] = mapped_column(String(16))  # shared | solo | persona_only
    transcript: Mapped[list] = mapped_column(JSONBCompat, default=list)  # [{i, speaker, text, fact_refs}]
    report: Mapped[dict] = mapped_column(JSONBCompat, default=dict)  # MatchingReport JSON
    # 쓴 fact 의 id 와 그 시점 문장 사본 — fact 가 나중에 바뀌거나 지워져도 결과를 재현할 수 있게
    topics_used: Mapped[list] = mapped_column(JSONBCompat, default=list)
    quality_warnings: Mapped[list] = mapped_column(JSONBCompat, default=list)
    # 노드별 시간·토큰·이슈 type·줄 번호·fact id. 문장 원문은 넣지 않는다
    harness_trace: Mapped[list] = mapped_column(JSONBCompat, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
