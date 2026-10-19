"""페르소나 추출 — API 입출력 모델."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.features.persona.schemas import SCORED, ConversationStyle


# DELETE /style 요청 — 대화 스타일 삭제 및 온보딩 점수 복원
class StyleDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"user_id": "user-456"}]})

    user_id: str = Field(min_length=1, max_length=64)


# POST /conversation 응답 — 작업이 만들어졌다. 결과는 GET /jobs/{job_id}
class ExtractionJobCreated(BaseModel):
    job_id: str
    status: Literal["pending"]


# GET /jobs/{job_id} 응답
class ExtractionJobResponse(BaseModel):
    job_id: str
    kind: Literal["conversation"]
    status: Literal["pending", "running", "succeeded", "failed"]
    persona_id: str | None = None  # 성공하면 새로 만든 (확정된) 페르소나 버전
    analyzed_count: int = 0
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


# LLM 이 관찰한 점수 한 개 — 값과 근거 발화 번호(프롬프트에 붙인 [번호])
class ScoreObservation(BaseModel):
    value: int = Field(ge=0, le=100)
    evidence: list[int] = Field(default_factory=list)


# ConversationAgent 의 원본 출력. 모르는 차원·범위 밖 값·형식이 틀린 항목은 그 항목만 버린다
class V2Extraction(BaseModel):
    model_config = {"extra": "ignore"}

    scores: dict[str, ScoreObservation] = Field(default_factory=dict)
    style: ConversationStyle = Field(default_factory=ConversationStyle)

    @field_validator("scores", mode="before")
    @classmethod
    def _clean_scores(cls, v: object) -> dict[str, dict]:
        if not isinstance(v, dict):
            return {}
        out: dict[str, dict] = {}
        for key, item in v.items():
            if isinstance(item, ScoreObservation):
                item = item.model_dump()
            if key not in SCORED or not isinstance(item, dict):
                continue
            value = item.get("value")
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
                continue
            raw = item.get("evidence")
            evidence = (
                [e for e in raw if isinstance(e, int) and not isinstance(e, bool)] if isinstance(raw, list) else []
            )
            out[key] = {"value": value, "evidence": evidence}
        return out
