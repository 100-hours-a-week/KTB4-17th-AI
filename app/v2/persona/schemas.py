"""추출 및 Reconcile 입출력 스키마."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

FactCategory = Literal["취미", "가치관", "일상", "연애관", "과거이력"]
ReconcileActionType = Literal["ADD", "UPDATE", "DELETE", "PROMOTE", "NOOP"]
# 코어 프로필로 받는 슬롯. LLM 이 이 밖의 키를 내도 graph 가 버린다
CORE_SLOTS = ("job", "region", "love_style", "personality", "marriage_plan")


class ExtractedFactItem(BaseModel):
    category: FactCategory = "일상"
    fact: str = Field(min_length=2, max_length=200)
    confidence: float = Field(ge=0.0, le=1.0)
    source_message_id: int | None = None

    @field_validator("category", mode="before")
    @classmethod
    def unknown_category_is_daily(cls, value: object) -> object:
        """LLM 이 정의에 없는 카테고리를 내도 작업 전체가 실패하지 않게 '일상' 으로 둔다."""
        return value if value in ("취미", "가치관", "일상", "연애관", "과거이력") else "일상"


class ExtractionOutput(BaseModel):
    core_updates: dict[str, str | None] = Field(default_factory=dict)
    facts: list[ExtractedFactItem] = Field(default_factory=list)


class ReconcileDecision(BaseModel):
    new_fact_index: int
    action: ReconcileActionType
    target_id: int | None = None
    updated_fact: str | None = None


class ReconcileBatchOutput(BaseModel):
    decisions: list[ReconcileDecision] = Field(default_factory=list)


class FactItemResponse(BaseModel):
    id: int
    category: str | None
    fact: str
    confidence: float | None
    status: str
    updated_at: datetime


class UserMemoryResponse(BaseModel):
    user_id: str
    core_profile: dict[str, Any]
    last_processed_message_id: int
    facts: list[FactItemResponse]
