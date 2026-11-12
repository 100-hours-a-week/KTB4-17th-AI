from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints, field_validator, model_validator

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Utterance = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class FeedbackAnswer(StrEnum):
    YES = "YES"
    NO = "NO"


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FeedbackCardNextRequest(_Request):
    user_id: Identifier


class FeedbackCardResponse(BaseModel):
    card_id: Identifier
    context_question: ShortText | None
    utterance: Utterance
    question: ShortText
    expires_at: AwareDatetime


class FeedbackCardAnswerRequest(_Request):
    user_id: Identifier
    answer: FeedbackAnswer
    correction_text: ShortText | None = None
    answered_at: AwareDatetime
    consent_version: Identifier

    @field_validator("answered_at", mode="before")
    @classmethod
    def iso_datetime(cls, value: object) -> datetime:
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str):
            raise ValueError("answered_at must be an ISO 8601 date-time with timezone")
        return datetime.fromisoformat(value)

    @model_validator(mode="after")
    def correction_only_for_no(self) -> Self:
        if self.answer != FeedbackAnswer.NO and self.correction_text is not None:
            raise ValueError("correction_text is allowed only when answer is NO")
        return self


class FeedbackCardAnswerResponse(BaseModel):
    card_id: Identifier
    status: Literal["ANSWERED"] = "ANSWERED"
