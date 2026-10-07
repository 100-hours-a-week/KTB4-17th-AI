from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class Grade(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    RETRYABLE = "RETRYABLE"
    BLOCK = "BLOCK"


class Domain(StrEnum):
    IDENTITY = "IDENTITY"
    PERSPECTIVE = "PERSPECTIVE"
    FACT = "FACT"
    STYLE = "STYLE"
    TASK = "TASK"


class Violation(BaseModel):
    domain: Domain
    rule_id: str
    description: str
    severity: Grade


class CheckResult(BaseModel):
    grade: Grade
    violations: list[Violation]
    latency_ms: int


class ValidationResult(BaseModel):
    status: Literal["PASS", "WARN", "REGENERATED", "FALLBACK", "SHADOW_FAIL"]
    grade: Grade
    initial_grade: Grade
    regenerated: bool
    violations: list[Violation]
    latency_ms: int


class GuardrailContext(BaseModel):
    surface: Literal[
        "persona_turn",
        "practice_reply",
        "simulation_line",
        "simulation_report",
        "persona_narrative",
        "chat_end_message",
    ]
    speaker_name: str
    partner_name: str | None = None
    speaker_attributes: list[str] = []
    partner_attributes: list[str] = []
    user_texts: list[str] = []
    style: Literal["formal", "report"] = "formal"
    max_chars: int = 400
    avoid_expressions: list[str] = []
    asked_if_ai: bool = False
    task: Literal["single_turn", "first_turn_json", "script_line", "report", "narrative"] = "single_turn"


class AppliedText(BaseModel):
    text: str
    result: ValidationResult | None
