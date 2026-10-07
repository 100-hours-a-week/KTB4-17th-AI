from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.features.simulation.schemas import DEFAULT_TURNS, MAX_TURNS, MIN_TURNS, MatchingReport

# ══ 시작 요청 ═════════════════════════════════════════════════


class MigrationStartRequest(BaseModel):
    """마이그레이션 시작 본문. 실제로 쓰는 상대는 partner_user_id 하나다.

    기존 클라이언트가 partner_persona_id, partner_session_id 를 같이 넣어도 422 로
    거절하지 않고 버린다. 상대를 찾는 값은 partner_user_id 만 쓴다.
    """

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "examples": [
                {
                    "me_user_id": "dummy-user-a",
                    "partner_user_id": "dummy-user-b",
                    "turns": 3,
                }
            ]
        },
    )

    me_user_id: str = Field(min_length=1, max_length=64)
    partner_user_id: str = Field(min_length=1, max_length=64)
    turns: int = Field(default=DEFAULT_TURNS, ge=MIN_TURNS, le=MAX_TURNS)


# ══ 조회 및 API 응답 모델 ═════════════════════════════════════


class MigrationAccepted(BaseModel):
    """시뮬레이션 시작(POST) 및 재개(resume) 수락 응답 (202 Accepted)."""

    simulation_id: str


class MigrationUtteranceView(BaseModel):
    """단일 발화 조회 모델."""

    index: int
    speaker: Literal["a", "b"]
    nickname: str
    text: str = Field(max_length=300)


class MigrationRunView(BaseModel):
    """시뮬레이션 단건 실행 상태 조회 모델."""

    simulation_id: str
    status: Literal["running", "reporting", "done", "failed", "aborted"]
    turns: int
    attempt: int
    error_reason: str | None = None
    narrative_source: Literal["llm", "template"] | None = None
    utterances: list[MigrationUtteranceView] = Field(default_factory=list)


class MigrationReportView(BaseModel):
    """시뮬레이션 완료 리포트 조회 모델."""

    simulation_id: str
    report: MatchingReport
    narrative_source: Literal["llm", "template"] | None = None


class MigrationListItem(BaseModel):
    """시뮬레이션 목록 항목 모델."""

    simulation_id: str
    status: Literal["running", "reporting", "done", "failed", "aborted"]
    turns: int
    created_at: datetime


# ══ SSE 이벤트 페이로드 모델 ════════════════════════════════════
# event 리터럴은 run, utterance, report, done, error만 허용하며 delta 필드는 없다.


class MigrationRunEvent(BaseModel):
    """SSE run 이벤트 페이로드."""

    event: Literal["run"] = "run"
    simulation_id: str
    turns: int
    status: Literal["running", "reporting", "done", "failed", "aborted"] | str


class MigrationUtteranceEvent(BaseModel):
    """SSE utterance 이벤트 페이로드 (id는 대사 index)."""

    event: Literal["utterance"] = "utterance"
    id: int
    index: int
    speaker: Literal["a", "b"]
    nickname: str
    text: str = Field(max_length=300)


class MigrationReportEvent(BaseModel):
    """SSE report 이벤트 페이로드."""

    event: Literal["report"] = "report"
    report: MatchingReport


class MigrationDoneEvent(BaseModel):
    """SSE done 이벤트 페이로드."""

    event: Literal["done"] = "done"
    simulation_id: str
    status: Literal["done"] = "done"


class MigrationErrorEvent(BaseModel):
    """SSE error 이벤트 페이로드."""

    event: Literal["error"] = "error"
    reason: str
    message: str


# 별칭 지원
RunEvent = MigrationRunEvent
UtteranceEvent = MigrationUtteranceEvent
ReportEvent = MigrationReportEvent
DoneEvent = MigrationDoneEvent
ErrorEvent = MigrationErrorEvent

MigrationSSEPayload = (
    MigrationRunEvent | MigrationUtteranceEvent | MigrationReportEvent | MigrationDoneEvent | MigrationErrorEvent
)
