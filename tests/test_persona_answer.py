"""온보딩 답변 — 짧은 답·빈 답·질문과 무관한 답에 대한 대응."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.features.persona import api
from app.features.persona.schemas import TurnResponse

def _client():
    calls = []

    async def get_session(session_id):
        return SimpleNamespace(id=session_id, pending_topic_id="weekend")

    class FakeService:
        repo = SimpleNamespace(get_session=get_session)

        async def submit_answer(self, session, answer):
            calls.append(answer)
            return TurnResponse(session_id=session.id, utterance="다음 질문", progress="3/10")

    class FakeDb:
        async def commit(self):
            pass

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_service] = lambda: FakeService()
    app.dependency_overrides[get_db] = lambda: FakeDb()
    return TestClient(app), calls


def _answer(client, answer):
    return client.post("/v1/persona/onboarding/s1/answer", json={"answer": answer})


def test_one_character_answer_is_accepted_and_trimmed():
    client, calls = _client()

    res = _answer(client, " 네 ")

    assert res.status_code == 200
    assert calls == ["네"]


@pytest.mark.parametrize(
    ("answer", "code", "message"),
    [
        ("", "answer_empty", "조금 더 길게 입력해 주시면 페르소나를 더 정확하게 만들 수 있어요. 다시 답해 주세요."),
        ("   ", "answer_empty", "조금 더 길게 입력해 주시면 페르소나를 더 정확하게 만들 수 있어요. 다시 답해 주세요."),
        ("가" * 201, "answer_too_long", "답변은 200자 이내로 입력해 주세요. 다시 답해 주세요."),
    ],
    ids=["empty", "whitespace", "too-long"],
)
def test_invalid_answer_is_422_with_message_to_show_and_question_kept(answer, code, message):
    client, calls = _client()

    res = _answer(client, answer)

    assert res.status_code == 422
    assert res.json()["detail"] == {"code": code, "message": message}
    assert calls == []  # 질문은 그대로 — 사용자가 바로 다시 보낼 수 있다
