from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.features.persona.schemas import PersonaResponse


class MigrationState(BaseModel):
    """시뮬레이션 마이그레이션 그래프의 메모리 상태.

    DB 세션, 이벤트 큐, asyncio 객체는 필드나 기본값으로 두지 않는다.
    """

    run_id: str
    attempt: int
    persona_a: PersonaResponse
    persona_b: PersonaResponse
    nickname_a: str
    nickname_b: str
    turns: int
    transcript: list[tuple[Literal["a", "b"], str]] = Field(default_factory=list)
    error: str | None = None
