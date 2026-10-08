"""페르소나 추출 API — service 는 가짜, 상태코드·응답·백그라운드 예약만 본다."""

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.db import get_db
from app.features.persona.schemas import PersonaResponse
from app.features.persona_extraction import api
from app.features.persona_extraction.parsers import UnknownFormat
from app.features.persona_extraction.service import (
    JobInProgress,
    JobNotFound,
    NoStyleToDelete,
    NothingToExtract,
    PersonaNotFound,
    SpeakerNotFound,
    TooFewUtterances,
)

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def _job(**kw):
    base = dict(
        id="j1",
        kind="practice",
        status="pending",
        persona_id=None,
        analyzed_count=0,
        error=None,
        created_at=NOW,
        finished_at=None,
    )
    return SimpleNamespace(**{**base, **kw})


def _client(error=None, job=None):
    calls = []

    class FakeService:
        async def start_practice(self, user_id):
            calls.append(("practice", user_id))
            if error:
                raise error
            return _job()

        async def start_conversation(self, user_id, speaker_name, raw):
            calls.append(("conversation", user_id, speaker_name, raw))
            if error:
                raise error
            return _job(kind="conversation")

        async def get_job(self, job_id):
            calls.append(("get", job_id))
            if error:
                raise error
            return job or _job()

        async def delete_style(self, user_id):
            calls.append(("delete_style", user_id))
            if error:
                raise error
            return PersonaResponse(
                persona_id="p-reset",
                version=3,
                is_confirmed=True,
                source="reset",
                scores={"disclosure": 70},
                confidence={"disclosure": "HIGH"},
                conversation_style=None,
                generated_at=NOW,
            )

    class FakeDb:
        async def commit(self):
            calls.append(("commit",))

    async def fake_runner(job_id):
        calls.append(("run", job_id))

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_service] = lambda: FakeService()
    app.dependency_overrides[get_db] = lambda: FakeDb()
    app.dependency_overrides[api.get_runner] = lambda: fake_runner
    return TestClient(app), calls


URL = "/v1/persona-extraction"
FORM = {"user_id": "u1", "speaker_name": "민수"}


def test_practice_returns_202_commits_then_runs_in_background():
    client, calls = _client()

    res = client.post(f"{URL}/practice", json={"user_id": "u1"})

    assert res.status_code == 202
    assert res.json() == {"job_id": "j1", "status": "pending"}
    assert calls == [("practice", "u1"), ("commit",), ("run", "j1")]


def test_conversation_accepts_text():
    client, calls = _client()

    res = client.post(f"{URL}/conversation", data={**FORM, "text": "대화"})

    assert res.status_code == 202
    assert calls[0] == ("conversation", "u1", "민수", "대화")


def test_conversation_accepts_utf8_bom_file():
    client, calls = _client()

    res = client.post(
        f"{URL}/conversation", data=FORM, files={"file": ("talk.txt", "\ufeff대화".encode(), "text/plain")}
    )

    assert res.status_code == 202
    assert calls[0][3] == "대화"


def test_conversation_needs_exactly_one_of_file_or_text():
    client, calls = _client()

    none = client.post(f"{URL}/conversation", data=FORM)
    both = client.post(f"{URL}/conversation", data={**FORM, "text": "a"}, files={"file": ("t.txt", b"b", "text/plain")})

    assert none.status_code == 422 and both.status_code == 422
    assert calls == []


def test_conversation_too_large_is_413(monkeypatch):
    monkeypatch.setattr(get_settings(), "extraction_max_upload_bytes", 3)
    client, calls = _client()

    res = client.post(f"{URL}/conversation", data={**FORM, "text": "가나다"})

    assert res.status_code == 413
    assert calls == []


def test_domain_errors_map_to_status_codes_without_commit_or_run():
    cases = [
        (PersonaNotFound("u1"), 404),
        (JobInProgress("j0"), 409),
        (NothingToExtract("u1"), 409),
        (SpeakerNotFound("철수"), 422),
        (TooFewUtterances(3), 422),
        (UnknownFormat("x"), 422),
    ]
    for error, status in cases:
        client, calls = _client(error=error)
        res = client.post(f"{URL}/conversation", data={**FORM, "text": "a"})
        assert res.status_code == status, error
        assert ("commit",) not in calls and not any(c[0] == "run" for c in calls)


def test_job_in_progress_tells_running_job_id():
    client, _ = _client(error=JobInProgress("j0"))

    res = client.post(f"{URL}/practice", json={"user_id": "u1"})

    assert res.json()["detail"] == {"message": "이미 진행 중인 추출이 있어요", "job_id": "j0"}


def test_get_job_returns_status_and_404():
    client, _ = _client(job=_job(status="succeeded", persona_id="p2", analyzed_count=30, finished_at=NOW))

    res = client.get(f"{URL}/jobs/j1")

    assert res.status_code == 200
    assert res.json()["status"] == "succeeded" and res.json()["persona_id"] == "p2"

    client, _ = _client(error=JobNotFound("x"))
    assert client.get(f"{URL}/jobs/x").status_code == 404


def test_delete_style_success():
    client, calls = _client()

    res = client.request("DELETE", f"{URL}/style", json={"user_id": "u1"})

    assert res.status_code == 200
    assert res.json()["persona_id"] == "p-reset"
    assert res.json()["source"] == "reset"
    assert res.json()["conversation_style"] is None
    assert calls == [("delete_style", "u1"), ("commit",)]


def test_delete_style_no_style_error():
    client, _ = _client(error=NoStyleToDelete("삭제할 대화 스타일이 없어요"))

    res = client.request("DELETE", f"{URL}/style", json={"user_id": "u1"})

    assert res.status_code == 400
    assert res.json()["detail"] == "삭제할 대화 스타일이 없어요"
