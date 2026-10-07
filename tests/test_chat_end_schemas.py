import pytest
from pydantic import ValidationError

from app.features.chat_end.schemas import (
    ChatEndDraftsRequest,
    ChatEndDraftsResponse,
    ChatEndMessageRequest,
    EndType,
    Speaker,
)


def _recent(n: int = 2) -> list[dict]:
    pair = [
        {"speaker": "REQUESTER", "content": "요즘 바쁘신가 봐요."},
        {"speaker": "TARGET", "content": "네 좀 정신없었어요."},
    ]
    return [pair[i % 2] for i in range(n)]


def _drafts_body(**over) -> dict:
    body = {
        "room_id": 5001,
        "delegation_id": 7001,
        "requester_user_id": "user-1",
        "end_type": "GENTLE",
        "recent_messages": _recent(),
    }
    body.update(over)
    return body


def _message_body(**over) -> dict:
    body = {
        "room_id": 5001,
        "delegation_id": 7001,
        "user_id": "user-1",
        "target_user_id": "user-2",
        "end_type": "GENTLE",
        "recent_messages": _recent(),
        "ending_messages": ["바쁘신 와중에 연락해 주셔서 고마웠어요."],
    }
    body.update(over)
    return body


def test_drafts_request_parses_valid_body():
    req = ChatEndDraftsRequest.model_validate(_drafts_body())
    assert req.end_type is EndType.GENTLE
    assert req.recent_messages[0].speaker is Speaker.REQUESTER


def test_message_request_strips_whitespace():
    req = ChatEndMessageRequest.model_validate(
        _message_body(ending_messages=["  고마웠어요.  "], recent_messages=[{"speaker": "TARGET", "content": " 네 "}])
    )
    assert req.ending_messages == ["고마웠어요."]
    assert req.recent_messages[0].content == "네"


@pytest.mark.parametrize(
    "over",
    [
        {"recent_messages": []},
        {"recent_messages": _recent(21)},
        {"recent_messages": [{"speaker": "TARGET", "content": "   "}]},
        {"recent_messages": [{"speaker": "TARGET", "content": "가" * 501}]},
        {"recent_messages": [{"speaker": "OTHER", "content": "안녕"}]},
        {"end_type": "ANGRY"},
        {"room_id": 0},
        {"requester_user_id": ""},
        {"requester_user_id": "u" * 65},
        {"memberId": 1},
    ],
)
def test_drafts_request_rejects_invalid(over):
    with pytest.raises(ValidationError):
        ChatEndDraftsRequest.model_validate(_drafts_body(**over))


@pytest.mark.parametrize(
    "over",
    [
        {"ending_messages": []},
        {"ending_messages": ["a", "b", "c", "d"]},
        {"ending_messages": ["가" * 301]},
        {"ending_messages": ["   "]},
        {"target_user_id": ""},
    ],
)
def test_message_request_rejects_invalid(over):
    with pytest.raises(ValidationError):
        ChatEndMessageRequest.model_validate(_message_body(**over))


def test_drafts_response_requires_exactly_three():
    ChatEndDraftsResponse(room_id=1, ending_messages=["a", "b", "c"])
    with pytest.raises(ValidationError):
        ChatEndDraftsResponse(room_id=1, ending_messages=["a", "b"])
