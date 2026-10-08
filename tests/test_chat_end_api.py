from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.features.chat_end import api
from app.features.chat_end.schemas import (
    ChatEndConfirmResponse,
    ChatEndDraftsResponse,
    ChatEndMessageResponse,
    ChatEndStatus,
    DraftStatus,
)
from app.features.chat_end.service import DraftLocked, DraftNotFound, EndTypeMismatch, NoConfirmedDraft

RECENT = [{"speaker": "REQUESTER", "content": "요즘 바쁘신가 봐요."}]
DRAFT_ID = "a" * 32


def _client(*, raises: Exception | None = None):
    calls = []

    class FakeService:
        async def create_drafts(self, req):
            calls.append(("drafts", req.requester_user_id))
            return ChatEndDraftsResponse(
                room_id=req.room_id,
                draft_id=DRAFT_ID,
                status=DraftStatus.PENDING,
                turns=[{"turn": i, "message": f"{i}번째 답변"} for i in (1, 2, 3)],
            )

        async def confirm_draft(self, draft_id):
            calls.append(("confirm", draft_id))
            if raises:
                raise raises
            return ChatEndConfirmResponse(draft_id=draft_id, delegation_id=7001, status=DraftStatus.CONFIRMED)

        async def create_message(self, req):
            calls.append(("message", req.user_id))
            if raises:
                raise raises
            return ChatEndMessageResponse(
                room_id=req.room_id,
                message_id=1,
                ai_response="고마웠어요.",
                status=ChatEndStatus.SUCCESS,
                ai_turn_count=1,
                is_final=False,
                end_turns=1,
                end_reason="상호 합의 종료",
            )

    class FakeDb:
        async def commit(self):
            calls.append(("commit",))

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_service] = lambda: FakeService()
    app.dependency_overrides[get_db] = lambda: FakeDb()
    return TestClient(app), calls


def _drafts_body():
    return {
        "room_id": 5001,
        "delegation_id": 7001,
        "requester_user_id": "user-1",
        "end_type": "GENTLE",
        "recent_messages": RECENT,
    }


def _message_body():
    return {
        "room_id": 5001,
        "delegation_id": 7001,
        "user_id": "user-1",
        "target_user_id": "user-2",
        "end_type": "GENTLE",
        "recent_messages": RECENT,
    }


def test_drafts_returns_script_200_and_commits():
    client, calls = _client()
    res = client.post("/v1/chat_end/drafts", json=_drafts_body())
    assert res.status_code == 200
    assert res.json() == {
        "room_id": 5001,
        "draft_id": DRAFT_ID,
        "status": "PENDING",
        "turns": [
            {"turn": 1, "message": "1번째 답변"},
            {"turn": 2, "message": "2번째 답변"},
            {"turn": 3, "message": "3번째 답변"},
        ],
    }
    assert calls == [("drafts", "user-1"), ("commit",)]


def test_confirm_returns_200_and_commits():
    client, calls = _client()
    res = client.post(f"/v1/chat_end/drafts/{DRAFT_ID}/confirm")
    assert res.status_code == 200
    assert res.json() == {"draft_id": DRAFT_ID, "delegation_id": 7001, "status": "CONFIRMED"}
    assert calls == [("confirm", DRAFT_ID), ("commit",)]


def test_confirm_unknown_draft_is_404_without_commit():
    client, calls = _client(raises=DraftNotFound("x"))
    res = client.post(f"/v1/chat_end/drafts/{DRAFT_ID}/confirm")
    assert res.status_code == 404
    assert ("commit",) not in calls


def test_confirm_after_messages_started_is_409_without_commit():
    client, calls = _client(raises=DraftLocked("x"))
    res = client.post(f"/v1/chat_end/drafts/{DRAFT_ID}/confirm")
    assert res.status_code == 409
    assert ("commit",) not in calls


def test_messages_returns_200_and_commits():
    client, calls = _client()
    res = client.post("/v1/chat_end/messages", json=_message_body())
    assert res.status_code == 200
    assert res.json() == {
        "room_id": 5001,
        "message_id": 1,
        "ai_response": "고마웠어요.",
        "status": "SUCCESS",
        "ai_turn_count": 1,
        "is_final": False,
        "end_turns": 1,
        "end_reason": "상호 합의 종료",
    }
    assert calls == [("message", "user-1"), ("commit",)]


def test_messages_without_confirmed_draft_is_409_without_commit():
    client, calls = _client(raises=NoConfirmedDraft(7001))
    res = client.post("/v1/chat_end/messages", json=_message_body())
    assert res.status_code == 409
    assert ("commit",) not in calls


def test_messages_with_mismatched_end_type_is_409_without_commit():
    client, calls = _client(raises=EndTypeMismatch("GENTLE", "DIRECT"))
    res = client.post("/v1/chat_end/messages", json={**_message_body(), "end_type": "DIRECT"})
    assert res.status_code == 409
    assert ("commit",) not in calls


def test_messages_rejects_the_removed_ending_messages_field():
    client, calls = _client()
    res = client.post("/v1/chat_end/messages", json={**_message_body(), "ending_messages": ["더 이상 받지 않는다"]})
    assert res.status_code == 422
    assert calls == []


def test_invalid_body_returns_422_without_calling_service():
    client, calls = _client()
    res = client.post("/v1/chat_end/drafts", json={"room_id": 1, "memberId": 1})
    assert res.status_code == 422
    assert calls == []
