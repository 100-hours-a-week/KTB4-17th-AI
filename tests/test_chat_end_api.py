import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.core.guardrail_trace  # noqa: F401  (guardrail trace 테이블도 create_all 에 포함)
from app.core.db import Base, get_db
from app.features.chat_end import agents, api
from app.features.chat_end.schemas import (
    ChatEndConfirmResponse,
    ChatEndDraftsResponse,
    ChatEndMessageResponse,
    ChatEndStatus,
    DraftStatus,
)
from app.features.chat_end.service import (
    DraftLocked,
    DraftNotFound,
    EndTypeMismatch,
    MessageGenerationFailed,
    NoConfirmedDraft,
)

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


@pytest.mark.parametrize("bad_id", ["short", "a" * 40, "A" * 32, "a" * 31 + "%00"])
def test_confirm_rejects_malformed_draft_id_with_422(bad_id):
    client, calls = _client()
    res = client.post(f"/v1/chat_end/drafts/{bad_id}/confirm")
    assert res.status_code == 422
    assert calls == []


def test_openapi_documents_error_responses():
    app = FastAPI()
    app.include_router(api.router)
    paths = app.openapi()["paths"]
    confirm = paths["/v1/chat_end/drafts/{draft_id}/confirm"]["post"]["responses"]
    messages = paths["/v1/chat_end/messages"]["post"]["responses"]
    assert "404" in confirm and "409" in confirm
    assert "409" in messages and "503" in messages


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


def test_messages_generation_failure_is_503_without_fallback_text_but_commits_the_failed_row():
    client, calls = _client(raises=MessageGenerationFailed(1))
    res = client.post("/v1/chat_end/messages", json=_message_body())
    assert res.status_code == 503
    assert set(res.json()) == {"detail"}  # 폴백 문장·status 필드를 본문에 싣지 않는다
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
    assert "GENTLE" in res.json()["detail"] and "DIRECT" in res.json()["detail"]
    assert ("commit",) not in calls


SCRIPT = [
    "대화 나눌 수 있어서 고마웠어요. 다만 저는 여기서 인연을 마무리하려고 해요.",
    "마음 써 주셔서 감사해요. 그래도 제 마음은 변하지 않았어요.",
    "그동안 정말 고마웠어요. 더 이상 답장하지 않고 이 채팅은 여기서 종료할게요.",
]


def test_golden_path_drafts_confirm_then_three_messages_through_http(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_MODE", "off")
    replies = [
        json.dumps({"turns": SCRIPT}, ensure_ascii=False),
        json.dumps({"ai_response": "고마웠어요. 여기서 마무리할게요.", "end_reason": "r"}, ensure_ascii=False),
        json.dumps(
            {"ai_response": "마음 써 줘서 고마워요. 제 생각은 그대로예요.", "end_reason": "r"}, ensure_ascii=False
        ),
        json.dumps(
            {
                "ai_response": "그동안 고마웠어요.",
                "closing": "더 이상 답장하지 않고 이 채팅은 여기서 종료할게요.",
                "end_reason": "r",
            },
            ensure_ascii=False,
        ),
    ]

    async def fake_call(**_):
        return replies.pop(0)

    monkeypatch.setattr(agents, "_call", fake_call)
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def real_db():
        async with factory() as db:
            yield db

    async def create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = real_db  # get_service 는 덮지 않는다 — 진짜 service·repository 를 쓴다
    msg = _message_body()
    with TestClient(app) as client:
        client.portal.call(create_tables)  # 앱과 같은 이벤트 루프에서 테이블을 만든다
        draft_id = client.post("/v1/chat_end/drafts", json=_drafts_body()).json()["draft_id"]
        assert client.post("/v1/chat_end/messages", json=msg).status_code == 409
        assert client.post(f"/v1/chat_end/drafts/{draft_id}/confirm").status_code == 200
        assert client.post("/v1/chat_end/messages", json={**msg, "end_type": "DIRECT"}).status_code == 409
        turns = [client.post("/v1/chat_end/messages", json=msg).json() for _ in range(3)]
    assert [(t["ai_turn_count"], t["is_final"]) for t in turns] == [(1, False), (2, False), (3, True)]


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
