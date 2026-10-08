"""페르소나 추출 — API 입출력 모델."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# POST /practice 요청 — 연습대화의 미반영 내 발화로 추출
class PracticeExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"user_id": "user-456"}]})

    user_id: str = Field(min_length=1, max_length=64)


# DELETE /style 요청 — 대화 스타일 삭제 및 온보딩 점수 복원
class StyleDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{"user_id": "user-456"}]})

    user_id: str = Field(min_length=1, max_length=64)


# POST /practice · /conversation 응답 — 작업이 만들어졌다. 결과는 GET /jobs/{job_id}
class ExtractionJobCreated(BaseModel):
    job_id: str
    status: Literal["pending"]


# GET /jobs/{job_id} 응답
class ExtractionJobResponse(BaseModel):
    job_id: str
    kind: Literal["practice", "conversation"]
    status: Literal["pending", "running", "succeeded", "failed"]
    persona_id: str | None = None  # 성공하면 새로 만든 (확정된) 페르소나 버전
    analyzed_count: int = 0
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
