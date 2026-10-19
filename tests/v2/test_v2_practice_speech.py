"""연습대화 말 놓기 제안 — 10번째 발화 뒤 제안, 허락이면 반말 모드, 거절이면 다시 묻지 않는다."""

import asyncio

import pytest

from app.features.persona.agents import LLMError
from app.features.practice.agents import PartnerAgent
from app.features.practice.schemas import PracticeStartRequest
from app.v2.persona.repository import PersonaMemoryRepository
from app.v2.practice import service as service_mod
from app.v2.practice import speech
from app.v2.practice.models import V2PracticeSpeechState
from app.v2.practice.service import V2PracticeService
from tests.conftest import seed_persona, with_db

VEC = [1.0, 0.0] + [0.0] * 1022


class FakeEmbedder:
    async def encode_query(self, text):
        return VEC

    async def encode(self, text):
        return VEC

    async def encode_batch(self, texts):
        return [VEC for _ in texts]


class ScriptedPartner(PartnerAgent):
    """시스템 프롬프트를 붙잡아 두고, 정해진 답을 낸다."""

    def __init__(self, reply="오 그렇군요 ㅎㅎ"):
        self.reply_text = reply
        self.systems: list[str] = []

    async def reply(self, *, system, history, opening=False, trace_metadata=None):
        self.systems.append(system)
        yield self.reply_text


def _allow(value: bool):
    async def fake(answer: str) -> bool:
        return value

    return fake


async def _seed(db):
    await seed_persona(db, persona_id="p-partner", user_id="pu1", nickname="지민")
    await seed_persona(db, persona_id="p-me", user_id="u1", nickname="민수")


async def _start(factory):
    async with factory() as db:
        result = await V2PracticeService(db, embedder=FakeEmbedder()).start(
            PracticeStartRequest(partner_user_id="pu1", me_user_id="u1")
        )
        await db.commit()
        return result.session_id


async def _say(factory, session_id, agent, message="오늘 뭐 하셨어요"):
    """요청 하나를 흉내낸다: 새 DB 세션에서 세션을 읽고 내 메시지를 보낸다."""
    async with factory() as db:
        service = V2PracticeService(db, embedder=FakeEmbedder())
        service.agent = agent
        session = await service.repo.get_session(session_id)
        _ = [ev async for ev in service.stream_reply(session, message)]


async def _mode(factory, session_id):
    async with factory() as db:
        state = await db.get(V2PracticeSpeechState, session_id)
        return state.mode if state else None


def _scenario(steps):
    async def body(factory):
        async with factory() as db:
            await _seed(db)
        return await steps(factory)

    return asyncio.run(with_db(body))


def test_asks_permission_detects_the_proposal_phrase():
    assert speech.asks_permission("저도 재밌어요 ㅎㅎ 말 편하게 해도 될까요?")
    assert speech.asks_permission("우리 말 편하게 해도 돼요?")
    assert not speech.asks_permission("주말엔 뭐 하세요?")


def test_speech_addon_by_mode():
    formal = speech.speech_addon(me="민수", mode="formal", ask_now=False)
    ask = speech.speech_addon(me="민수", mode="formal", ask_now=True)
    declined = speech.speech_addon(
        me="민수", mode="declined", ask_now=True
    )  # 거절한 사용자에게는 제안 지시가 없어야 한다
    casual = speech.speech_addon(me="민수", mode="casual", ask_now=False)

    assert "존댓말" in formal and speech.ASK_PHRASE not in formal
    assert speech.ASK_PHRASE in ask
    assert "원하지 않았습니다" in declined and speech.ASK_PHRASE not in declined
    assert "반말로 말합니다" in casual and "존댓말(해요체)" not in casual


@pytest.mark.parametrize(
    ("llm_result", "expected"),
    [({"allow": True}, True), ({"allow": False}, False), ({"allow": "yes"}, False), ({}, False)],
)
def test_classify_permission_only_true_is_allow(monkeypatch, llm_result, expected):
    async def fake(**kw):
        return llm_result

    monkeypatch.setattr(speech, "_call_json", fake)

    assert asyncio.run(speech.classify_permission("네 좋아요")) is expected


def test_classify_permission_llm_failure_is_no(monkeypatch):
    async def boom(**kw):
        raise LLMError("timeout")

    monkeypatch.setattr(speech, "_call_json", boom)

    assert asyncio.run(speech.classify_permission("네")) is False


def test_stays_formal_and_does_not_ask_before_ten_user_turns():
    agent = ScriptedPartner()

    async def steps(factory):
        sid = await _start(factory)
        for _ in range(9):
            await _say(factory, sid, agent)
        return await _mode(factory, sid)

    mode = _scenario(steps)

    assert mode == "formal"
    assert all(speech.ASK_PHRASE not in s for s in agent.systems)
    assert all("존댓말(해요체)" in s for s in agent.systems)  # 허락 전에는 존댓말 고정


def test_tenth_turn_asks_and_moves_to_asked():
    agent = ScriptedPartner(reply=f"그렇군요 ㅎㅎ {speech.ASK_PHRASE}")

    async def steps(factory):
        sid = await _start(factory)
        for _ in range(10):
            await _say(factory, sid, agent)
        return await _mode(factory, sid)

    assert _scenario(steps) == "asked"
    assert speech.ASK_PHRASE in agent.systems[9] and speech.ASK_PHRASE not in agent.systems[8]


def test_asked_is_not_set_if_the_reply_did_not_actually_ask_and_it_tries_again():
    agent = ScriptedPartner(reply="그렇군요 ㅎㅎ")

    async def steps(factory):
        sid = await _start(factory)
        for _ in range(10):
            await _say(factory, sid, agent)
        after_ten = await _mode(factory, sid)
        agent.reply_text = f"그렇군요 {speech.ASK_PHRASE}"
        await _say(factory, sid, agent)
        return after_ten, await _mode(factory, sid)

    assert _scenario(steps) == ("formal", "asked")


def test_yes_switches_to_casual_speech_mode(monkeypatch):
    monkeypatch.setattr(service_mod, "classify_permission", _allow(True))
    agent = ScriptedPartner(reply=f"그렇군요 {speech.ASK_PHRASE}")

    async def steps(factory):
        sid = await _start(factory)
        for _ in range(10):
            await _say(factory, sid, agent)
        agent.reply_text = "그럼 편하게 할게 ㅎㅎ"
        await _say(factory, sid, agent, message="네 좋아요")
        mode = await _mode(factory, sid)
        await _say(factory, sid, agent)
        async with factory() as db:
            core = await PersonaMemoryRepository(db).get_core("u1")
        return mode, core

    mode, core = _scenario(steps)

    assert mode == "casual"
    assert "반말로 말합니다" in agent.systems[10] and "반말로 말합니다" in agent.systems[11]
    assert speech.ASK_PHRASE not in agent.systems[11]
    assert core is None or core.casual_speech_declined_at is None  # 허락은 사용자 단위로 남기지 않는다


def test_no_marks_declined_and_never_asks_again_even_in_a_new_session(monkeypatch):
    monkeypatch.setattr(service_mod, "classify_permission", _allow(False))
    agent = ScriptedPartner(reply=f"그렇군요 {speech.ASK_PHRASE}")

    async def steps(factory):
        sid = await _start(factory)
        for _ in range(10):
            await _say(factory, sid, agent)
        await _say(factory, sid, agent, message="아직은 존댓말이 편해요")
        first = await _mode(factory, sid)
        for _ in range(12):  # 이 세션에서 더 대화해도 묻지 않는다
            await _say(factory, sid, agent)
        async with factory() as db:
            core = await PersonaMemoryRepository(db).get_core("u1")
        declined_at = core.casual_speech_declined_at

        agent.systems.clear()
        sid2 = await _start(factory)  # 같은 사용자의 새 세션
        for _ in range(11):
            await _say(factory, sid2, agent)
        return first, declined_at, await _mode(factory, sid2)

    first, declined_at, second_mode = _scenario(steps)

    assert first == "declined" and declined_at is not None
    assert second_mode == "formal"  # 새 세션은 상태가 없지만 사용자 거절 때문에 제안 지시가 나가지 않는다
    assert all(speech.ASK_PHRASE not in s for s in agent.systems)
    assert all("원하지 않았습니다" in s for s in agent.systems)


def test_session_without_user_id_tracks_state_per_session_only(monkeypatch):
    monkeypatch.setattr(service_mod, "classify_permission", _allow(False))
    agent = ScriptedPartner(reply=f"그렇군요 {speech.ASK_PHRASE}")

    async def steps(factory):
        async with factory() as db:
            result = await V2PracticeService(db, embedder=FakeEmbedder()).start(
                PracticeStartRequest(partner_user_id="pu1", nickname="손님")
            )
            await db.commit()
            sid = result.session_id
        for _ in range(10):
            await _say(factory, sid, agent)
        await _say(factory, sid, agent, message="아니요")
        return await _mode(factory, sid)

    assert _scenario(steps) == "declined"  # user_id 가 없어도 세션 안에서는 다시 묻지 않는다
