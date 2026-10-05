"""AI 생성 사진 판별 응답 모델."""

from enum import StrEnum

from pydantic import BaseModel, Field


class SyntheticDecision(StrEnum):
    CLEAR = "CLEAR"
    SYNTHETIC_RISK = "SYNTHETIC_RISK"
    CONFIRMED_SYNTHETIC = "CONFIRMED_SYNTHETIC"


class SyntheticResult(BaseModel):
    decision: SyntheticDecision
    probability: float | None = Field(default=None, ge=0, le=1)
    provenance: str
    model_version: str
    signals: list[str] = Field(default_factory=list)
