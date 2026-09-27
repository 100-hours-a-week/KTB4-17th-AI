from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.features.persona.schemas import PersonaBrief
from app.features.practice import api
from app.features.practice.schemas import DeltaEvent, DoneEvent, PracticeStartResponse, StartEvent
from app.features.practice.service import PersonaNotFound, SessionEnded

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _client(sessions=None, start_error=None):
    sessions = sessions or {}
    calls = []

    async def get_session(session_id):
        return sessions.get(session_id)

    class FakeService:
        repo = SimpleNamespace(get_session=get_session)

        async def start(self, req):
            calls.append(("start", req.partner.describe()))
            if start_error:
                raise start_error
            return PracticeStartResponse(
                session_id="s1",
                partner=PersonaBrief(persona_id="p1", nickname="지수"),
                my_nickname="회원",
                created_at=NOW,
            )

        async def stream_reply(self, session, message):
            calls.append(("reply", message))
            yield "start", StartEvent(session_id=session.id, message_index=1)
            yield "delta", DeltaEvent(text="안녕")
            yield "done", DoneEvent(session_id=session.id, message_index=1, content="안녕", source="llm")

        async def stream_retry(self, session):
            calls.append(("retry",))
            yield "done", DoneEvent(session_id=session.id, message_index=1, content="다시", source="llm")

        async def stream_opening(self, session):
            calls.append(("opening",))
            raise SessionEnded(session.id)
            yield  # pragma: no cover

    class FakeDb:
        async def commit(self):
            calls.append(("commit",))

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_service] = lambda: FakeService()
    app.dependency_overrides[api.get_stream_service] = lambda: FakeService()
    app.dependency_overrides[get_db] = lambda: FakeDb()
    return TestClient(app), calls


ACTIVE = SimpleNamespace(id="s1", status="active")


def test_message_of_only_whitespace_is_rejected():
    client, calls = _client({"s1": ACTIVE})

    res = client.post("/v1/practice/s1/messages", json={"message": "   "})

    assert res.status_code == 422
    assert calls == []


def test_message_is_sent_trimmed():
    client, calls = _client({"s1": ACTIVE})

    client.post("/v1/practice/s1/messages", json={"message": "  주말에 뭐 해요?  "})

    assert calls == [("reply", "주말에 뭐 해요?")]


def test_message_streams_start_delta_done_as_sse():
    client, _ = _client({"s1": ACTIVE})

    res = client.post("/v1/practice/s1/messages", json={"message": "안녕하세요"})

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert res.text == (
        'event: start\ndata: {"session_id":"s1","message_index":1}\n\n'
        'event: delta\ndata: {"text":"안녕"}\n\n'
        'event: done\ndata: {"session_id":"s1","message_index":1,"content":"안녕","source":"llm"}\n\n'
    )


def test_message_to_unknown_session_is_404():
    client, calls = _client()

    res = client.post("/v1/practice/nope/messages", json={"message": "안녕하세요"})

    assert res.status_code == 404
    assert calls == []


def test_message_to_ended_session_is_409():
    client, calls = _client({"s1": SimpleNamespace(id="s1", status="ended")})

    res = client.post("/v1/practice/s1/messages", json={"message": "안녕하세요"})

    assert res.status_code == 409
    assert calls == []


def test_session_ended_during_stream_becomes_error_event():
    client, _ = _client({"s1": ACTIVE})

    res = client.post("/v1/practice/s1/opening")

    assert res.status_code == 200
    assert res.text == 'event: error\ndata: {"detail": "session ended"}\n\n'


def test_start_creates_session_and_commits():
    client, calls = _client()

    res = client.post("/v1/practice/start", json={"partner": {"persona_id": "p1"}})

    assert res.status_code == 201
    assert res.json()["session_id"] == "s1"
    assert calls == [("start", "p1"), ("commit",)]


def test_start_with_missing_partner_is_404_without_commit():
    ref = SimpleNamespace(describe=lambda: "p404")
    client, calls = _client(start_error=PersonaNotFound("partner", ref))

    res = client.post("/v1/practice/start", json={"partner": {"persona_id": "p404"}})

    assert res.status_code == 404
    assert res.json()["detail"] == "partner: 저장된 페르소나가 없어요 (p404)"
    assert ("commit",) not in calls


def test_start_with_two_partner_refs_is_422():
    client, calls = _client()

    res = client.post("/v1/practice/start", json={"partner": {"persona_id": "p1", "user_id": "u1"}})

    assert res.status_code == 422
    assert calls == []


def _msg(role):
    return SimpleNamespace(role=role)


def test_retry_streams_reply_for_unanswered_message():
    client, calls = _client({"s1": SimpleNamespace(id="s1", status="active", messages=[_msg("user")])})

    res = client.post("/v1/practice/s1/retry")

    assert res.status_code == 200
    assert res.text == 'event: done\ndata: {"session_id":"s1","message_index":1,"content":"다시","source":"llm"}\n\n'
    assert calls == [("retry",)]


def test_retry_with_nothing_unanswered_is_409():
    answered = SimpleNamespace(id="s1", status="active", messages=[_msg("user"), _msg("persona")])
    client, calls = _client({"s1": answered, "s2": SimpleNamespace(id="s2", status="active", messages=[])})

    assert client.post("/v1/practice/s1/retry").status_code == 409
    assert client.post("/v1/practice/s2/retry").status_code == 409
    assert calls == []


def test_retry_on_unknown_or_ended_session():
    client, calls = _client({"s1": SimpleNamespace(id="s1", status="ended", messages=[_msg("user")])})

    assert client.post("/v1/practice/nope/retry").status_code == 404
    assert client.post("/v1/practice/s1/retry").status_code == 409
    assert calls == []
