"""채팅 종료 — API 입출력 모델.

  POST /v1/chat_end/drafts                    : 1·2·3번째 답변이 담긴 마무리 스크립트 1벌 (PENDING 으로 저장)
  POST /v1/chat_end/drafts/{draft_id}/confirm : 스크립트 확정 (PENDING → CONFIRMED)
  POST /v1/chat_end/messages                  : 확정 스크립트의 N번째 문장을 방향으로 삼은 최종 종료 메시지 1개

messages 는 스크립트를 요청으로 받지 않는다. delegation_id 로 DB 에서 확정 스크립트를 찾는다.
recent_messages 는 프롬프트에만 쓰고 저장하지 않는다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

MAX_MESSAGE_LEN = 1000  # practice MAX_MESSAGE_LEN 과 같다
MAX_RECENT_MESSAGES = 20
# 스크립트 한 문장·종료 메시지 한 건의 글자 수 한도. 문장 수는 제한하지 않는다. 마지막 턴은 본문 + closing 을 합쳐서 센다
# 검사는 agents/service 에서 하고 모델 검증에는 걸지 않는다 (초과는 경미한 문제라 500 으로 만들지 않는다)
MAX_REPLY_CHARS = 200
MAX_REASON_LEN = 255
MAX_AI_TURNS = 3  # 종료 위임에서 AI 가 답장하는 최대 횟수. 마지막 턴은 채팅을 닫는다

UserId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class EndType(StrEnum):
    GENTLE = "GENTLE"
    DIRECT = "DIRECT"
    CASUAL = "CASUAL"


class Speaker(StrEnum):
    REQUESTER = "REQUESTER"
    TARGET = "TARGET"


class ChatEndStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class DraftStatus(StrEnum):
    # 현재 확정본이 아니다 (새로 만들었거나, 다른 초안이 확정되며 내려왔다). messages 는 쓰지 못한다
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"  # 확정됐다. 같은 delegation_id 에 1개만 있다


class _Request(BaseModel):
    # 모르는 필드(예: 문서의 memberId)는 조용히 버리지 않고 422 로 거절한다
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# 최근 대화 한 줄. 요청자(REQUESTER)와 상대(TARGET) 중 누가 말했는지
class RecentMessage(_Request):
    speaker: Speaker
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_LEN)


# POST /drafts 요청 바디
class ChatEndDraftsRequest(_Request):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "room_id": 5001,
                    "delegation_id": 7001,
                    "requester_user_id": "user-1001",
                    "end_type": "GENTLE",
                    "recent_messages": [
                        {"speaker": "REQUESTER", "content": "요즘 바쁘신가 봐요."},
                        {"speaker": "TARGET", "content": "네 좀 정신없었어요."},
                    ],
                }
            ]
        },
    )

    room_id: int = Field(ge=1)
    delegation_id: int = Field(ge=1)
    requester_user_id: UserId
    end_type: EndType
    recent_messages: list[RecentMessage] = Field(min_length=1, max_length=MAX_RECENT_MESSAGES)


# 스크립트의 한 턴. turn=1 이 첫 번째 답변
class ScriptTurn(BaseModel):
    turn: int = Field(ge=1, le=MAX_AI_TURNS)
    message: str = Field(min_length=1)


# POST /drafts 응답 — 스크립트 1벌. 고르는 단계는 없다. 확정은 별도 API
class ChatEndDraftsResponse(BaseModel):
    room_id: int
    draft_id: str
    status: DraftStatus
    turns: list[ScriptTurn] = Field(min_length=MAX_AI_TURNS, max_length=MAX_AI_TURNS)


# POST /drafts/{draft_id}/confirm 응답
class ChatEndConfirmResponse(BaseModel):
    draft_id: str
    delegation_id: int
    status: DraftStatus


# POST /messages 요청 바디. 스크립트는 받지 않는다 (delegation_id 로 DB 에서 확정본을 찾는다)
class ChatEndMessageRequest(_Request):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "room_id": 5001,
                    "delegation_id": 7001,
                    "user_id": "user-1001",
                    "target_user_id": "user-2001",
                    "end_type": "GENTLE",
                    "recent_messages": [
                        {"speaker": "REQUESTER", "content": "요즘 바쁘신가 봐요."},
                        {"speaker": "TARGET", "content": "네 좀 정신없었어요."},
                    ],
                }
            ]
        },
    )

    room_id: int = Field(ge=1)
    delegation_id: int = Field(ge=1)
    user_id: UserId
    target_user_id: UserId
    end_type: EndType
    recent_messages: list[RecentMessage] = Field(min_length=1, max_length=MAX_RECENT_MESSAGES)


# POST /messages 응답. 실패해도 200 — status=FAILED, ai_response 는 확정 스크립트의 N번째 문장
# ai_turn_count: 이번 답장이 몇 번째인지(1~MAX_AI_TURNS). FAILED 는 턴을 쓰지 않아 재시도하면 같은 번호
# is_final: 성공한 마지막 턴이라 채팅을 종료해야 한다. end_turns: 성공이면 ai_turn_count, 실패면 0
class ChatEndMessageResponse(BaseModel):
    room_id: int
    message_id: int
    ai_response: str = Field(min_length=1)
    status: ChatEndStatus
    ai_turn_count: int = Field(ge=1, le=MAX_AI_TURNS)
    is_final: bool
    end_turns: int = Field(ge=0, le=MAX_AI_TURNS)
    end_reason: str | None = None
