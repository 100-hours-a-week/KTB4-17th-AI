"""연습대화 — API 입출력 모델 + SSE 이벤트.

SSE 는 `text/event-stream`. 이벤트 이름과 data(JSON) 는 아래 *Event 모델 그대로:

  event: start   data: {"session_id", "message_index"}          — 페르소나 답변 시작
  event: delta   data: {"text"}                                  — 토큰 조각. 이어 붙이면 전문
  event: done    data: {"session_id", "message_index", "content", "source"}  — 끝. content 는 전문
  event: error   data: {"detail"}                                — 실패. 이 뒤로 delta 없음
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.guardrail import ValidationResult
from app.features.persona.schemas import PersonaBrief, PersonaRef

MAX_MESSAGE_LEN = 500
# LLM 에 넘기는 최근 메시지 수. 더 오래된 건 잊는다 — 연습 대화는 길어도 한 세션 안의 얘기라 이 정도면 맥락이 산다
HISTORY_WINDOW = 40


# POST /start 요청 바디 — 상대(필수)와 나(선택)를 user_id 로 지정. 둘 다 확정된 페르소나가 있어야 한다.
class PracticeStartRequest(BaseModel):
    # 예전 partner/me(PersonaRef) 필드는 조용히 무시하지 않고 422로 거절한다
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"partner_user_id": "user-123", "me_user_id": "user-456"}]},
    )

    partner_user_id: str = Field(min_length=1, max_length=64)  # 상대 사용자
    me_user_id: str | None = Field(default=None, min_length=1, max_length=64)  # 있으면 상대가 나를 조금 "안다"
    # me_user_id 가 없을 때만 쓰는 내 이름. me_user_id 가 있으면 내 온보딩 닉네임이 우선한다
    nickname: str | None = Field(default=None, min_length=1, max_length=20)

    def partner_ref(self) -> PersonaRef:
        return PersonaRef(user_id=self.partner_user_id)

    def me_ref(self) -> PersonaRef | None:
        return PersonaRef(user_id=self.me_user_id) if self.me_user_id else None


# POST /start 응답 — 새로 만든 세션 정보
class PracticeStartResponse(BaseModel):
    session_id: str
    partner: PersonaBrief
    my_nickname: str
    message_count: int = 0
    created_at: datetime


# POST /{id}/messages 요청 바디 — 내가 보내는 메시지 한 줄.
# session_id 는 미리 만들어져 있어야 한다(지금은 /start) — 없으면 404.
class PracticeMessageRequest(BaseModel):
    # 공백만 보낸 메시지는 앞뒤를 잘라 빈 문자열로 만든 뒤 min_length 에서 422 로 막는다
    model_config = {"str_strip_whitespace": True}

    message: str = Field(
        min_length=1,
        max_length=MAX_MESSAGE_LEN,
        examples=["주말에 보통 뭐 하고 지내세요?"],
        description="내가 보낼 메시지",
    )


# GET /{id} 응답에 들어가는 메시지 한 건
class PracticeMessageItem(BaseModel):
    index: int
    role: Literal["user", "persona"]
    content: str
    created_at: datetime


# GET / 응답의 한 줄 — 내 연습대화 목록용
class PracticeSessionSummary(BaseModel):
    session_id: str
    partner: PersonaBrief
    my_nickname: str
    status: str
    message_count: int
    created_at: datetime
    updated_at: datetime


# GET /{id}, POST /{id}/end 응답 — 세션 + 전체 메시지 이력
class PracticeSessionResponse(BaseModel):
    session_id: str
    partner: PersonaBrief
    my_nickname: str
    status: str
    messages: list[PracticeMessageItem]
    created_at: datetime


# POST /{id}/opening · /messages · /retry 응답 — 답변을 모아서 한 번에. SSE 의 done 이벤트와 같은 모양
class PracticeReplyResponse(BaseModel):
    session_id: str
    message_index: int
    content: str
    source: Literal["llm", "fallback"]
    validationResult: ValidationResult | None = None


# ── SSE 이벤트 data ────────────────────────────────────────


# 페르소나 답변 스트리밍 시작 알림
class StartEvent(BaseModel):
    session_id: str
    message_index: int


# 답변 토큰 한 조각
class DeltaEvent(BaseModel):
    text: str


# 답변 스트리밍 완료 — content 는 조각을 이어붙인 전문
class DoneEvent(BaseModel):
    session_id: str
    message_index: int
    content: str
    source: Literal["llm", "fallback"]
    validationResult: ValidationResult | None = None


# 스트리밍 중 실패 알림 (이 뒤로 delta 없음)
class ErrorEvent(BaseModel):
    detail: str
