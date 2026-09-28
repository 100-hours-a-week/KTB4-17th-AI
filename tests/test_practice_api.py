from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.features.persona.schemas import PersonaBrief
from app.features.practice import api
from app.features.practice.schemas import (
    DeltaEvent,
    DoneEvent,
    ErrorEvent,
    PracticeSessionSummary,
    PracticeStartResponse,
    StartEvent,
)
from app.features.practice.service import (
    ConcurrentRequest,
    NothingToRetry,
    PersonaNotFound,
    SessionEnded,
)

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _client(sessions=None, start_error=None, stream_error=None, listed=None, error_event=None):
    sessions = sessions or {}
    calls = []

    async def get_session(session_id):
        return sessions.get(session_id)

    class FakeService:
        repo = SimpleNamespace(get_session=get_session)

        async def start(self, req):
            calls.append(("start", req.partner_user_id))
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

        async def list_for_user(self, user_id, limit):
            calls.append(("list", user_id, limit))
            return listed or []

        async def stream_retry(self, session):
            calls.append(("retry",))
            if stream_error:
                raise stream_error
            if error_event:
                yield "start", StartEvent(session_id=session.id, message_index=1)
                yield "error", ErrorEvent(detail=error_event)
                return
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


ACTIVE = SimpleNamespace(id="s1", status="active", messages=[])


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

    res = client.post("/v1/practice/s1/messages/stream", json={"message": "안녕하세요"})

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

    res = client.post("/v1/practice/s1/opening/stream")

    assert res.status_code == 200
    assert res.text == 'event: error\ndata: {"detail": "session ended"}\n\n'


def test_start_creates_session_and_commits():
    client, calls = _client()

    res = client.post("/v1/practice/start", json={"partner_user_id": "u1"})

    assert res.status_code == 201
    assert res.json()["session_id"] == "s1"
    assert calls == [("start", "u1"), ("commit",)]


def test_start_with_missing_partner_is_404_without_commit():
    ref = SimpleNamespace(describe=lambda: "p404")
    client, calls = _client(start_error=PersonaNotFound("partner", ref))

    res = client.post("/v1/practice/start", json={"partner_user_id": "u404"})

    assert res.status_code == 404
    assert res.json()["detail"] == "partner: 확정된 페르소나가 없어요 (p404)"
    assert ("commit",) not in calls


def test_start_with_legacy_partner_ref_is_422():
    client, calls = _client()

    res = client.post("/v1/practice/start", json={"partner": {"user_id": "u1"}})

    assert res.status_code == 422
    assert calls == []


def test_start_without_partner_user_id_is_422():
    client, calls = _client()

    res = client.post("/v1/practice/start", json={"me_user_id": "u2"})

    assert res.status_code == 422
    assert calls == []


def _msg(role):
    return SimpleNamespace(role=role)


def test_retry_streams_reply_for_unanswered_message():
    client, calls = _client({"s1": SimpleNamespace(id="s1", status="active", messages=[_msg("user")])})

    res = client.post("/v1/practice/s1/retry/stream")

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


def test_opening_on_session_with_messages_is_409():
    client, calls = _client({"s1": SimpleNamespace(id="s1", status="active", messages=[_msg("persona")])})

    res = client.post("/v1/practice/s1/opening")

    assert res.status_code == 409
    assert res.json()["detail"] == "opening already done"
    assert calls == []


def test_retry_race_becomes_error_event():
    client, _ = _client(
        {"s1": SimpleNamespace(id="s1", status="active", messages=[_msg("user")])},
        stream_error=NothingToRetry("s1"),
    )

    res = client.post("/v1/practice/s1/retry/stream")

    assert res.status_code == 200
    assert res.text == 'event: error\ndata: {"detail": "nothing to retry"}\n\n'


def test_concurrent_request_becomes_error_event():
    client, _ = _client(
        {"s1": SimpleNamespace(id="s1", status="active", messages=[_msg("user")])},
        stream_error=ConcurrentRequest(),
    )

    res = client.post("/v1/practice/s1/retry/stream")

    assert res.status_code == 200
    assert res.text.startswith("event: error\ndata: ")
    assert "다른 요청을 처리 중이에요" in res.text


def test_list_sessions_passes_user_id_and_limit():
    row = PracticeSessionSummary(
        session_id="s1",
        partner=PersonaBrief(persona_id="p1", nickname="지수"),
        my_nickname="민수",
        status="active",
        message_count=2,
        created_at=NOW,
        updated_at=NOW,
    )
    client, calls = _client(listed=[row])

    res = client.get("/v1/practice", params={"user_id": "u1", "limit": 5})

    assert res.status_code == 200
    assert res.json()[0]["session_id"] == "s1"
    assert calls == [("list", "u1", 5)]


def test_list_sessions_requires_user_id_and_valid_limit():
    client, calls = _client()

    assert client.get("/v1/practice").status_code == 422
    assert client.get("/v1/practice", params={"user_id": ""}).status_code == 422
    assert client.get("/v1/practice", params={"user_id": "u1", "limit": 101}).status_code == 422
    assert calls == []


def test_start_request_swagger_example_is_user_id_only():
    example = api.PracticeStartRequest.model_json_schema()["examples"][0]

    assert set(example) == {"partner_user_id", "me_user_id"}


# ── 일반(JSON) 버전 — 답변을 모아서 한 번에 ─────────────────


def test_message_returns_collected_reply_as_json():
    client, calls = _client({"s1": ACTIVE})

    res = client.post("/v1/practice/s1/messages", json={"message": "안녕하세요"})

    assert res.status_code == 200
    assert res.headers["content-type"] == "application/json"
    assert res.json() == {"session_id": "s1", "message_index": 1, "content": "안녕", "source": "llm"}
    assert calls == [("reply", "안녕하세요")]


def test_retry_returns_collected_reply_as_json():
    client, _ = _client({"s1": SimpleNamespace(id="s1", status="active", messages=[_msg("user")])})

    res = client.post("/v1/practice/s1/retry")

    assert res.status_code == 200
    assert res.json()["content"] == "다시"


def test_opening_json_maps_stream_errors_to_http_status():
    client, _ = _client({"s1": ACTIVE})  # 가짜 stream_opening 은 SessionEnded 를 낸다

    res = client.post("/v1/practice/s1/opening")

    assert res.status_code == 409
    assert res.json()["detail"] == "session ended"


def test_json_retry_race_is_409():
    client, _ = _client(
        {"s1": SimpleNamespace(id="s1", status="active", messages=[_msg("user")])},
        stream_error=NothingToRetry("s1"),
    )

    res = client.post("/v1/practice/s1/retry")

    assert res.status_code == 409
    assert res.json()["detail"] == "nothing to retry"


def test_json_concurrent_request_is_409():
    client, _ = _client(
        {"s1": SimpleNamespace(id="s1", status="active", messages=[_msg("user")])},
        stream_error=ConcurrentRequest(),
    )

    res = client.post("/v1/practice/s1/retry")

    assert res.status_code == 409
    assert "다른 요청을 처리 중이에요" in res.json()["detail"]


def test_json_persona_missing_is_404():
    ref = SimpleNamespace(describe=lambda: "p1")
    client, _ = _client(
        {"s1": SimpleNamespace(id="s1", status="active", messages=[_msg("user")])},
        stream_error=PersonaNotFound("partner", ref),
    )

    res = client.post("/v1/practice/s1/retry")

    assert res.status_code == 404
    assert res.json()["detail"] == "partner: 확정된 페르소나가 없어요"


def test_json_reply_broken_midway_is_503():
    client, _ = _client(
        {"s1": SimpleNamespace(id="s1", status="active", messages=[_msg("user")])},
        error_event="답변 도중 연결이 끊겼어요: connection reset",
    )

    res = client.post("/v1/practice/s1/retry")

    assert res.status_code == 503
    assert "연결이 끊겼어요" in res.json()["detail"]


def test_stream_variants_check_session_like_json_ones():
    client, calls = _client({"ended": SimpleNamespace(id="ended", status="ended", messages=[])})

    assert client.post("/v1/practice/nope/messages/stream", json={"message": "안녕하세요"}).status_code == 404
    assert client.post("/v1/practice/ended/messages/stream", json={"message": "안녕하세요"}).status_code == 409
    assert client.post("/v1/practice/ended/opening/stream").status_code == 409
    assert client.post("/v1/practice/ended/retry/stream").status_code == 409
    assert calls == []
