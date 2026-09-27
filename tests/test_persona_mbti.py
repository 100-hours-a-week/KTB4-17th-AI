"""MBTI 는 온보딩 시작 때 받는다 — 확정(/confirm) 요청이 오류로 MBTI 없이 오더라도 잃지 않게."""

import asyncio

import pytest
from conftest import NOW, with_db
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_persona_answer import FakeConversation
from test_persona_build_fallback import FakeExtraction

from app.core.db import get_db
from app.features.persona import api
from app.features.persona.repository import PersonaRepository
from app.features.persona.schemas import ConfirmPersonaResponse, RawExtraction, TurnResponse
from app.features.persona.service import OnboardingService, PersonaConfirmationConflict


def _onboard_and_confirm(*, start_mbti, confirm_mbti):
    """시작 → (10턴을 마친 상태로) build → confirm. 요청마다 새 DB 세션. 확정 응답과 저장된 페르소나 MBTI."""

    async def scenario(factory):
        def service(db):
            svc = OnboardingService(PersonaRepository(db))
            svc.conversation, svc.extraction = FakeConversation(), FakeExtraction(RawExtraction(avoidance=60))
            return svc

        async with factory() as db:
            first = await service(db).start("민수", "user-1", mbti=start_mbti)
            await db.commit()
        async with factory() as db:
            svc = service(db)
            session = await svc.repo.get_session(first.session_id)
            session.turn_index, session.pending_topic_id = session.total_turns, None  # 문답을 다 마친 상태
            draft = await svc.build_draft(session)
            await db.commit()
        async with factory() as db:
            confirmed = await service(db).confirm_persona(draft.persona_id, confirm_mbti, NOW)
            await db.commit()
        async with factory() as db:
            stored = await PersonaRepository(db).get_persona(draft.persona_id)
        return confirmed, stored.mbti

    return asyncio.run(with_db(scenario))


def test_mbti_given_at_start_is_saved_even_if_confirm_omits_it():
    confirmed, stored = _onboard_and_confirm(start_mbti="INTP", confirm_mbti=None)

    assert confirmed.mbti == "INTP"
    assert stored == "INTP"


def test_confirm_is_not_blocked_when_no_mbti_was_given_anywhere():
    confirmed, stored = _onboard_and_confirm(start_mbti=None, confirm_mbti=None)

    assert confirmed.is_confirmed is True
    assert (confirmed.mbti, stored) == (None, None)


def test_confirm_with_mbti_different_from_start_is_a_conflict():
    with pytest.raises(PersonaConfirmationConflict):
        _onboard_and_confirm(start_mbti="INTP", confirm_mbti="ENFJ")


@pytest.mark.parametrize(
    ("start_mbti", "confirm_mbti"),
    [("INTP", "INTP"), (None, "INTP")],
    ids=["same-both", "only-confirm (기존 클라이언트)"],
)
def test_confirm_mbti_still_accepted_when_consistent(start_mbti, confirm_mbti):
    confirmed, stored = _onboard_and_confirm(start_mbti=start_mbti, confirm_mbti=confirm_mbti)

    assert (confirmed.mbti, stored) == ("INTP", "INTP")


# ── API ────────────────────────────────────────────────


def _client():
    calls = []

    class FakeService:
        async def start(self, nickname, user_id, mbti=None):
            calls.append(("start", mbti))
            return TurnResponse(session_id="s1", utterance="질문1", progress="1/10")

        async def confirm_persona(self, persona_id, mbti, confirmed_at):
            calls.append(("confirm", mbti))
            return ConfirmPersonaResponse(
                persona_id=persona_id, user_id="user-1", is_confirmed=True, mbti=mbti, confirmed_at=NOW
            )

    class FakeDb:
        async def commit(self):
            calls.append(("commit",))

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_service] = lambda: FakeService()
    app.dependency_overrides[get_db] = lambda: FakeDb()
    return TestClient(app), calls


def _start(client, **extra):
    return client.post("/v1/persona/onboarding/start", json={"nickname": "민수", "user_id": "user-1", **extra})


def test_start_accepts_mbti_and_normalizes_case():
    client, calls = _client()

    res = _start(client, mbti=" intp ")

    assert res.status_code == 200
    assert calls[0] == ("start", "INTP")


def test_start_without_mbti_still_works():
    client, calls = _client()

    assert _start(client).status_code == 200
    assert calls[0] == ("start", None)


def test_start_with_unknown_mbti_is_422():
    client, calls = _client()

    assert _start(client, mbti="ABCD").status_code == 422
    assert calls == []


CONFIRM = {"persona_id": "p1", "is_confirmed": True, "confirmed_at": "2026-09-27T12:00:00Z"}


def test_confirm_may_omit_mbti():
    client, calls = _client()

    res = client.post("/v1/persona/p1/confirm", json=CONFIRM)

    assert res.status_code == 200
    assert calls == [("confirm", None), ("commit",)]
