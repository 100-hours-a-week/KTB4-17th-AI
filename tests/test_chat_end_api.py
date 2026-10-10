from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.features.chat_end import api
from app.features.chat_end.schemas import ChatEndDraftsResponse, ChatEndMessageResponse, ChatEndStatus

RECENT = [{"speaker": "REQUESTER", "content": "요즘 바쁘신가 봐요."}]


def _client():
    calls = []

    class FakeService:
        async def create_drafts(self, req):
            calls.append(("drafts", req.requester_user_id))
            return ChatEndDraftsResponse(room_id=req.room_id, ending_messages=["a", "b", "c"])

        async def create_message(self, req):
            calls.append(("message", req.user_id))
            return ChatEndMessageResponse(
                room_id=req.room_id,
                message_id=1,
                ai_response="고마웠어요.",
                status=ChatEndStatus.SUCCESS,
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


def test_drafts_returns_200_and_commits():
    client, calls = _client()
    res = client.post(
        "/v1/chat_end/drafts",
        json={
            "room_id": 5001,
            "delegation_id": 7001,
            "requester_user_id": "user-1",
            "end_type": "GENTLE",
            "recent_messages": RECENT,
        },
    )
    assert res.status_code == 200
    assert res.json() == {"room_id": 5001, "ending_messages": ["a", "b", "c"]}
    assert calls == [("drafts", "user-1"), ("commit",)]


def test_messages_returns_200_and_commits():
    client, calls = _client()
    res = client.post(
        "/v1/chat_end/messages",
        json={
            "room_id": 5001,
            "delegation_id": 7001,
            "user_id": "user-1",
            "target_user_id": "user-2",
            "end_type": "GENTLE",
            "recent_messages": RECENT,
            "ending_messages": ["고마웠어요."],
        },
    )
    assert res.status_code == 200
    assert res.json() == {
        "room_id": 5001,
        "message_id": 1,
        "ai_response": "고마웠어요.",
        "status": "SUCCESS",
        "end_turns": 1,
        "end_reason": "상호 합의 종료",
    }
    assert calls == [("message", "user-1"), ("commit",)]


def test_invalid_body_returns_422_without_calling_service():
    client, calls = _client()
    res = client.post("/v1/chat_end/drafts", json={"room_id": 1, "memberId": 1})
    assert res.status_code == 422
    assert calls == []
