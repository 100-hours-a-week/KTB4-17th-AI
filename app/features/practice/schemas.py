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

from pydantic import BaseModel, Field

from app.features.persona.schemas import PersonaBrief, PersonaRef

MAX_MESSAGE_LEN = 500
# LLM 에 넘기는 최근 메시지 수. 더 오래된 건 잊는다 — 연습 대화는 길어도 한 세션 안의 얘기라 이 정도면 맥락이 산다
HISTORY_WINDOW = 40


# POST /start 요청 바디 — 상대 페르소나(필수)와 내 페르소나(선택)를 지정
class PracticeStartRequest(BaseModel):
    partner: PersonaRef  # 상대 — 저장된 페르소나
    me: PersonaRef | None = None  # 내 페르소나. 있으면 상대가 나를 조금 "안다"
    nickname: str | None = Field(default=None, min_length=1, max_length=20)  # me 가 없을 때 내 이름


# POST /start 응답 — 새로 만든 세션 정보
class PracticeStartResponse(BaseModel):
    session_id: str
    partner: PersonaBrief
    my_nickname: str
    message_count: int = 0
    created_at: datetime


# POST /{id}/messages 요청 바디 — 내가 보내는 메시지 한 줄
class PracticeMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LEN)


# GET /{id} 응답에 들어가는 메시지 한 건
class PracticeMessageItem(BaseModel):
    index: int
    role: Literal["user", "persona"]
    content: str
    created_at: datetime


# GET /{id}, POST /{id}/end 응답 — 세션 + 전체 메시지 이력
class PracticeSessionResponse(BaseModel):
    session_id: str
    partner: PersonaBrief
    my_nickname: str
    status: str
    messages: list[PracticeMessageItem]
    created_at: datetime


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


# 스트리밍 중 실패 알림 (이 뒤로 delta 없음)
class ErrorEvent(BaseModel):
    detail: str
