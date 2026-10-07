"""채팅 종료 — API 입출력 모델.

  POST /v1/chat_end/drafts   : 마무리 초안 3개
  POST /v1/chat_end/messages : 고른 초안을 방향으로 삼아 최종 종료 메시지 1개

두 호출은 서로를 id 로 참조하지 않는다(stateless). 고른 초안은 요청 바디의 ending_messages 로 다시 받는다.
recent_messages 는 프롬프트에만 쓰고 저장하지 않는다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

MAX_MESSAGE_LEN = 500  # practice MAX_MESSAGE_LEN 과 같다
MAX_RECENT_MESSAGES = 20
MAX_ENDING_LEN = 300
MAX_ENDINGS = 3
MAX_REASON_LEN = 255

UserId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
EndingText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_ENDING_LEN)]


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


# POST /drafts 응답
class ChatEndDraftsResponse(BaseModel):
    room_id: int
    ending_messages: list[str] = Field(min_length=3, max_length=3)


# POST /messages 요청 바디. ending_messages 는 사용자가 고른 방향 초안(클라이언트 입력이라 신뢰하지 않는다)
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
                    "ending_messages": [
                        "바쁘신 와중에 연락해 주셔서 고마웠어요. 서로 좋은 인연으로 남으면 좋겠습니다."
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
    ending_messages: list[EndingText] = Field(min_length=1, max_length=MAX_ENDINGS)


# POST /messages 응답. 실패해도 200 — status=FAILED, ai_response 는 고른 초안 첫 문장
class ChatEndMessageResponse(BaseModel):
    room_id: int
    message_id: int
    ai_response: str = Field(min_length=1)
    status: ChatEndStatus
    end_turns: int = Field(ge=0)
    end_reason: str | None = None
