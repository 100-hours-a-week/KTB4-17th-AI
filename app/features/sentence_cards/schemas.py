"""문장 판정 카드 — API 입출력 모델. 계약: 문장_판정_카드_BE_연동_API.md · sentence_cards.schema.json

POST /v1/sentence-cards/next          : 유저에게 보여 줄 카드 1장 (없으면 204)
POST /v1/sentence-cards/answers/batch : BE 가 모아 둔 답변 묶음 (일부 거절돼도 200, 거절 이유는 rejected)
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

MAX_BATCH_ANSWERS = 1000

UserId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
CardId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class QuestionType(StrEnum):
    DIRECTION = "DIRECTION"  # 이 말은 그 차원의 어느 쪽인가
    AXIS = "AXIS"  # 이 말은 그 차원에 대한 이야기인가


class ChoiceId(StrEnum):
    HIGH = "high"
    LOW = "low"
    DIRECT = "direct"
    INFERRED = "inferred"
    NONE = "none"
    UNSURE = "unsure"


class CardStatus(StrEnum):
    SHOWN = "SHOWN"
    ANSWERED = "ANSWERED"


class Verdict(StrEnum):
    PENDING = "PENDING"
    AGREE = "AGREE"
    DISAGREE = "DISAGREE"
    AMBIGUOUS = "AMBIGUOUS"


class RejectReason(StrEnum):
    DUPLICATE_IN_BATCH = "DUPLICATE_IN_BATCH"
    CARD_NOT_FOUND = "CARD_NOT_FOUND"
    NOT_CARD_OWNER = "NOT_CARD_OWNER"
    CHOICE_NOT_IN_CARD = "CHOICE_NOT_IN_CARD"
    ANSWERED_AFTER_EXPIRY = "ANSWERED_AFTER_EXPIRY"
    ALREADY_ANSWERED = "ALREADY_ANSWERED"


class _Request(BaseModel):
    # 모르는 필드는 조용히 버리지 않고 422 로 거절한다
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SentenceCardChoice(BaseModel):
    choice_id: ChoiceId
    label: str = Field(min_length=1, max_length=100)


class SentenceCardNextRequest(_Request):
    request_id: UUID
    user_id: UserId


class SentenceCardResponse(BaseModel):
    card_id: str = Field(min_length=1, max_length=64)
    sentence: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=200)
    choices: list[SentenceCardChoice] = Field(min_length=3, max_length=4)
    expires_at: datetime


class SentenceCardAnswerItem(_Request):
    card_id: CardId
    user_id: UserId
    choice_id: ChoiceId
    answered_at: AwareDatetime  # 만료 판정 기준. 타임존 없는 값은 422


class SentenceCardAnswerBatchRequest(_Request):
    batch_id: UUID
    answers: list[SentenceCardAnswerItem] = Field(min_length=1, max_length=MAX_BATCH_ANSWERS)


class RejectedAnswer(BaseModel):
    card_id: str
    reason: RejectReason


class SentenceCardAnswerBatchResponse(BaseModel):
    batch_id: UUID
    received: int = Field(ge=1)
    accepted: int = Field(ge=0)
    rejected: list[RejectedAnswer]
