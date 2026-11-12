import pytest
from pydantic import ValidationError

from app.features.chat_end.schemas import (
    ChatEndConfirmResponse,
    ChatEndDraftsRequest,
    ChatEndDraftsResponse,
    ChatEndMessageRequest,
    ChatEndMessageResponse,
    ChatEndStatus,
    DraftStatus,
    EndType,
    ScriptTurn,
    Speaker,
)


def _recent(n: int = 2) -> list[dict]:
    pair = [
        {"speaker": "REQUESTER", "content": "요즘 바쁘신가 봐요."},
        {"speaker": "TARGET", "content": "네 좀 정신없었어요."},
    ]
    return [pair[i % 2] for i in range(n)]


REQUEST_ID = "550e8400-e29b-41d4-a716-446655440000"


def _drafts_body(**over) -> dict:
    body = {
        "request_id": REQUEST_ID,
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
        "request_id": REQUEST_ID,
        "room_id": 5001,
        "delegation_id": 7001,
        "user_id": "user-1",
        "target_user_id": "user-2",
        "end_type": "GENTLE",
        "recent_messages": _recent(),
    }
    body.update(over)
    return body


def test_drafts_request_parses_valid_body():
    req = ChatEndDraftsRequest.model_validate(_drafts_body())
    assert req.end_type is EndType.GENTLE
    assert req.recent_messages[0].speaker is Speaker.REQUESTER


@pytest.mark.parametrize("model,body", [(ChatEndDraftsRequest, _drafts_body), (ChatEndMessageRequest, _message_body)])
def test_request_id_is_normalized_to_lowercase(model, body):
    req = model.model_validate(body(request_id=" 550E8400-E29B-41D4-A716-446655440000 "))
    assert req.request_id == REQUEST_ID


@pytest.mark.parametrize("bad", [None, "", "not-a-uuid", "550e8400e29b41d4a716446655440000", 123])
@pytest.mark.parametrize("model,body", [(ChatEndDraftsRequest, _drafts_body), (ChatEndMessageRequest, _message_body)])
def test_request_id_must_be_a_standard_uuid(model, body, bad):
    with pytest.raises(ValidationError):
        model.model_validate(body(request_id=bad))


@pytest.mark.parametrize("model,body", [(ChatEndDraftsRequest, _drafts_body), (ChatEndMessageRequest, _message_body)])
def test_request_id_is_required(model, body):
    data = body()
    del data["request_id"]
    with pytest.raises(ValidationError):
        model.model_validate(data)


def test_failed_message_response_allows_empty_ai_response():
    res = ChatEndMessageResponse(
        room_id=5001,
        message_id=1,
        ai_response="",
        status=ChatEndStatus.FAILED,
        ai_turn_count=1,
        is_final=False,
        end_turns=0,
    )
    assert res.ai_response == ""


def test_message_request_strips_whitespace():
    req = ChatEndMessageRequest.model_validate(
        _message_body(recent_messages=[{"speaker": "TARGET", "content": " 네 "}], user_id=" user-1 ")
    )
    assert req.recent_messages[0].content == "네"
    assert req.user_id == "user-1"


@pytest.mark.parametrize(
    "over",
    [
        {"recent_messages": []},
        {"recent_messages": _recent(21)},
        {"recent_messages": [{"speaker": "TARGET", "content": "   "}]},
        {"recent_messages": [{"speaker": "TARGET", "content": "가" * 1001}]},
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
        {"recent_messages": []},
        {"recent_messages": _recent(21)},
        {"room_id": 0},
        {"delegation_id": 0},
        {"user_id": ""},
        {"user_id": "   "},
        {"target_user_id": "u" * 65},
        {"end_type": "ANGRY"},
        {"ending_messages": ["더 이상 받지 않는 필드"]},  # 스크립트는 DB 에서 찾는다
        {"memberId": 1},
    ],
)
def test_message_request_rejects_invalid(over):
    with pytest.raises(ValidationError):
        ChatEndMessageRequest.model_validate(_message_body(**over))


def _turns(n: int = 3) -> list[dict]:
    return [{"turn": i + 1, "message": f"{i + 1}번째 답변"} for i in range(n)]


def test_drafts_response_needs_exactly_three_turns():
    ok = ChatEndDraftsResponse(room_id=1, draft_id="a" * 32, status=DraftStatus.PENDING, turns=_turns())
    assert [t.turn for t in ok.turns] == [1, 2, 3]
    assert ok.status is DraftStatus.PENDING
    with pytest.raises(ValidationError) as e:
        ChatEndDraftsResponse(room_id=1, draft_id="a" * 32, status="PENDING", turns=_turns(2))
    assert e.value.errors()[0]["type"] == "too_short"
    # 상한 검증: 턴 번호는 모두 유효(1~3)하게 두고 길이만 4로 만든다
    four = _turns(3) + [{"turn": 1, "message": "x"}]
    with pytest.raises(ValidationError) as e:
        ChatEndDraftsResponse(room_id=1, draft_id="a" * 32, status="PENDING", turns=four)
    assert e.value.errors()[0]["type"] == "too_long"


@pytest.mark.parametrize("turn", [0, 4])
def test_script_turn_number_is_one_to_three(turn):
    with pytest.raises(ValidationError):
        ScriptTurn(turn=turn, message="x")


def test_script_turn_message_must_not_be_empty():
    with pytest.raises(ValidationError):
        ScriptTurn(turn=1, message="")


def test_confirm_response_shape():
    res = ChatEndConfirmResponse(draft_id="a" * 32, delegation_id=7001, status=DraftStatus.CONFIRMED)
    assert res.model_dump(mode="json") == {"draft_id": "a" * 32, "delegation_id": 7001, "status": "CONFIRMED"}
