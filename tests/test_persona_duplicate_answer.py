"""온보딩 답변 중복 방지 — 재전송(turn_index)과 동시 요청(세션 잠금)."""

import asyncio
from types import SimpleNamespace

import pytest
from conftest import with_db
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_persona_answer import FakeConversation, FakeTagging

from app.core.db import get_db
from app.features.persona import api
from app.features.persona.repository import PersonaRepository, SessionBusy
from app.features.persona.schemas import TurnResponse
from app.features.persona.service import OnboardingService, TurnMismatch


def _onboarding(*sends, tag_results=None):
    """온보딩을 시작하고 sends=[(답, turn_index)] 를 차례로 보낸다. 요청마다 새 DB 세션.

    (응답들, 저장된 턴 [(turn_index, 질문, 답)], 질문 생성 LLM 호출 수)"""

    async def scenario(factory):
        conversation = FakeConversation()
        tagging = FakeTagging(*(tag_results or [False] * len(sends)))

        def service(db):
            svc = OnboardingService(PersonaRepository(db))
            svc.conversation, svc.tagging = conversation, tagging
            return svc

        async with factory() as db:
            first = await service(db).start("민수", "user-1")
            await db.commit()
        responses = [first]
        for answer, turn_index in sends:
            async with factory() as db:
                svc = service(db)
                session = await svc.repo.get_session(first.session_id)
                responses.append(await svc.submit_answer(session, answer, turn_index=turn_index))
                await db.commit()
        async with factory() as db:
            session = await PersonaRepository(db).get_session(first.session_id)
            turns = [(t.turn_index, t.question, t.answer) for t in session.turns]
        return responses, turns, conversation.calls

    return asyncio.run(with_db(scenario))


def test_resent_answer_for_answered_turn_returns_current_question_without_saving():
    (first, answered, resent), turns, llm_calls = _onboarding(("주말엔 러닝해요", 0), ("주말엔 러닝해요", 0))

    assert first.turn_index == 0
    assert answered.turn_index == 1
    assert (resent.turn_index, resent.progress, resent.utterance) == (1, "2/10", "질문2")  # 첫 요청이 받았어야 할 응답
    assert turns == [(0, "질문1", "주말엔 러닝해요"), (1, "질문2", None)]  # 질문2 에 잘못 저장되지 않는다
    assert llm_calls == 2  # 재전송은 LLM 을 다시 부르지 않는다


def test_resent_last_answer_returns_closing_again():
    sends = [(f"답{i}", i) for i in range(10)] + [("답9", 9)]
    responses, turns, llm_calls = _onboarding(*sends)
    last, resent = responses[-2], responses[-1]

    assert last.done is True
    assert (resent.done, resent.utterance, resent.progress) == (True, last.utterance, "10/10")
    assert [a for _, _, a in turns] == [f"답{i}" for i in range(10)]  # 마지막 답이 두 번 저장되지 않는다
    assert llm_calls == 10


def test_answer_for_turn_not_asked_yet_is_rejected_without_saving():
    with pytest.raises(TurnMismatch):
        _onboarding(("주말엔 러닝해요", 3))


# ── API: 잠금 · 턴 번호 ───────────────────────────────────


def _client(session=None, busy=False, error=None):
    calls = []
    session = session or SimpleNamespace(id="s1", pending_topic_id="weekend", turn_index=1)

    async def lock_session(session_id):
        if busy:
            raise SessionBusy(session_id)
        return session

    class FakeService:
        repo = SimpleNamespace(lock_session=lock_session)

        async def submit_answer(self, session, answer, *, turn_index=None):
            calls.append(("answer", answer, turn_index))
            if error:
                raise error
            return TurnResponse(session_id=session.id, utterance="질문2", progress="2/10", turn_index=1)

        async def skip(self, session):
            calls.append(("skip",))
            return TurnResponse(session_id=session.id, utterance="질문3", progress="3/10")

        async def finish(self, session):
            calls.append(("finish",))
            return TurnResponse(session_id=session.id, utterance="끝", progress="2/2", done=True)

    class FakeDb:
        async def commit(self):
            calls.append(("commit",))

        async def rollback(self):
            calls.append(("rollback",))

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_service] = lambda: FakeService()
    app.dependency_overrides[get_db] = lambda: FakeDb()
    return TestClient(app), calls


def test_answer_passes_turn_index_to_service():
    client, calls = _client()

    res = client.post("/v1/persona/onboarding/s1/answer", json={"answer": "러닝해요", "turn_index": 0})

    assert res.status_code == 200
    assert res.json()["turn_index"] == 1
    assert calls == [("answer", "러닝해요", 0), ("commit",)]


BUSY = {"code": "request_in_progress", "message": "이전 요청을 처리하고 있어요. 잠시만 기다려 주세요."}


@pytest.mark.parametrize(
    ("path", "body"),
    [("answer", {"answer": "러닝해요"}), ("skip", None), ("finish", None)],
)
def test_request_while_session_is_being_processed_is_409_immediately(path, body):
    client, calls = _client(busy=True)

    res = client.post(f"/v1/persona/onboarding/s1/{path}", json=body)

    assert res.status_code == 409
    assert res.json()["detail"] == BUSY
    assert calls == []


def test_answer_for_turn_not_asked_yet_is_409():
    client, calls = _client(error=TurnMismatch())

    res = client.post("/v1/persona/onboarding/s1/answer", json={"answer": "러닝해요", "turn_index": 5})

    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "turn_mismatch"
    assert ("commit",) not in calls


def test_resent_last_answer_on_finished_session_is_replayed_not_409():
    finished = SimpleNamespace(id="s1", pending_topic_id=None, turn_index=10)
    client, calls = _client(session=finished)

    resent = client.post("/v1/persona/onboarding/s1/answer", json={"answer": "답9", "turn_index": 9})
    without_index = client.post("/v1/persona/onboarding/s1/answer", json={"answer": "답9"})

    assert resent.status_code == 200
    assert without_index.status_code == 409  # 턴 번호 없이 끝난 세션에 답하면 기존처럼 409
    assert calls[0] == ("answer", "답9", 9)


# ── 마지막 안전장치: 같은 턴 번호의 질문 행은 하나뿐 ─────────


def test_same_turn_cannot_be_asked_twice_in_db():
    async def scenario(factory):
        async with factory() as db:
            repo = PersonaRepository(db)
            session = await repo.create_session("민수", 10, "user-1")
            await repo.add_question(session, "weekend", "질문1", "llm")
            await repo.add_question(session, "interests", "질문1 다시", "llm")  # 잠금을 뚫은 동시 요청 흉내

    with pytest.raises(IntegrityError):
        asyncio.run(with_db(scenario))


def test_db_conflict_during_answer_is_409_and_rolled_back():
    client, calls = _client(error=IntegrityError("INSERT", {}, Exception("unique")))

    res = client.post("/v1/persona/onboarding/s1/answer", json={"answer": "러닝해요"})

    assert res.status_code == 409
    assert res.json()["detail"] == BUSY
    assert ("rollback",) in calls
    assert ("commit",) not in calls


class _PgError(Exception):
    def __init__(self, sqlstate):
        self.sqlstate = sqlstate


class _FailingDb:
    """execute 가 Postgres 오류를 내는 DB 세션 자리. SQLite 는 FOR UPDATE 를 무시해서 진짜로는 못 만든다."""

    def __init__(self, sqlstate):
        self.sqlstate = sqlstate
        self.rolled_back = False

    async def execute(self, stmt):
        raise DBAPIError("SELECT ... FOR UPDATE NOWAIT", {}, _PgError(self.sqlstate))

    async def rollback(self):
        self.rolled_back = True


def test_postgres_lock_not_available_becomes_session_busy():
    db = _FailingDb("55P03")

    with pytest.raises(SessionBusy):
        asyncio.run(PersonaRepository(db).lock_session("s1"))
    assert db.rolled_back is True


def test_other_db_errors_are_not_hidden_as_busy():
    with pytest.raises(DBAPIError):
        asyncio.run(PersonaRepository(_FailingDb("08006")).lock_session("s1"))  # 연결 끊김 등
