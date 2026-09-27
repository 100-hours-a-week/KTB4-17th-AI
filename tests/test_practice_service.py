import asyncio

import pytest
from conftest import seed_persona, with_db

from app.features.persona.schemas import PersonaRef
from app.features.practice.agents import FALLBACK_REPLY, OPENING_INSTRUCTION, LLMError, PartnerAgent
from app.features.practice.schemas import PracticeStartRequest
from app.features.practice.service import NothingToRetry, PersonaNotFound, PracticeService, SessionEnded


class FakePartner(PartnerAgent):
    """LLM 자리만 바꾼 상대 에이전트. chunks 를 차례로 내고, fail_after 번째 조각 뒤에 LLMError."""

    def __init__(self, chunks=("안녕", "하세요"), fail_after=None):
        self.chunks = chunks
        self.fail_after = fail_after
        self.calls = []

    async def reply(self, *, system, history, opening=False):
        self.calls.append({"history": history, "opening": opening})
        for i, chunk in enumerate(self.chunks):
            if i == self.fail_after:
                raise LLMError("connection reset")
            yield chunk
        if self.fail_after == len(self.chunks):
            raise LLMError("connection reset")


async def _collect(gen):
    return [(name, data.model_dump()) for name, data in [ev async for ev in gen]]


def _run(scenario):
    async def body(factory):
        async with factory() as db:
            await seed_persona(
                db, persona_id="partner", user_id="u-partner", nickname="지수", texts={"interests": ["러닝"]}
            )
            await seed_persona(db, persona_id="me", user_id="u-me", nickname="민수")
            await seed_persona(db, persona_id="draft", user_id="u-draft", nickname="초안", confirmed=False)
        return await scenario(factory)

    return asyncio.run(with_db(body))


async def _start(factory, **req):
    async with factory() as db:
        service = PracticeService(db)
        result = await service.start(PracticeStartRequest(partner=PersonaRef(persona_id="partner"), **req))
        await db.commit()
        return result


async def _stream(factory, session_id, agent, *, message=None, retry=False):
    """요청 하나를 흉내낸다: 새 DB 세션에서 세션을 읽고, 답변 스트림을 끝까지 소비한다."""
    async with factory() as db:
        service = PracticeService(db)
        service.agent = agent
        session = await service.repo.get_session(session_id)
        if retry:
            gen = service.stream_retry(session)
        elif message is None:
            gen = service.stream_opening(session)
        else:
            gen = service.stream_reply(session, message)
        return await _collect(gen)


async def _history(factory, session_id):
    async with factory() as db:
        service = PracticeService(db)
        view = await service.get(await service.repo.get_session(session_id))
        return [(m.index, m.role, m.content) for m in view.messages]


# ── start ─────────────────────────────────────────────


def test_start_without_me_calls_me_member():
    async def scenario(factory):
        return await _start(factory)

    result = _run(scenario)

    assert result.partner.nickname == "지수"
    assert result.my_nickname == "회원"
    assert result.message_count == 0


def test_start_with_my_persona_uses_my_onboarding_nickname():
    async def scenario(factory):
        return await _start(factory, me=PersonaRef(user_id="u-me"))

    assert _run(scenario).my_nickname == "민수"


def test_start_with_unconfirmed_partner_is_persona_not_found():
    async def scenario(factory):
        async with factory() as db:
            await PracticeService(db).start(PracticeStartRequest(partner=PersonaRef(persona_id="draft")))

    with pytest.raises(PersonaNotFound) as e:
        _run(scenario)
    assert e.value.who == "partner"


# ── 답변 스트리밍 ─────────────────────────────────────


def test_reply_streams_and_saves_both_messages():
    async def scenario(factory):
        sid = (await _start(factory)).session_id
        events = await _stream(factory, sid, FakePartner(), message="주말에 뭐 해요?")
        return sid, events, await _history(factory, sid)

    sid, events, history = _run(scenario)

    assert events == [
        ("start", {"session_id": sid, "message_index": 1}),
        ("delta", {"text": "안녕"}),
        ("delta", {"text": "하세요"}),
        ("done", {"session_id": sid, "message_index": 1, "content": "안녕하세요", "source": "llm"}),
    ]
    assert history == [(0, "user", "주말에 뭐 해요?"), (1, "persona", "안녕하세요")]


def test_llm_failure_before_first_chunk_uses_fallback_and_keeps_conversation():
    async def scenario(factory):
        sid = (await _start(factory)).session_id
        events = await _stream(factory, sid, FakePartner(fail_after=0), message="안녕하세요")
        return events, await _history(factory, sid)

    events, history = _run(scenario)

    assert events[-1][0] == "done"
    assert events[-1][1]["source"] == "fallback"
    assert events[-1][1]["content"] == FALLBACK_REPLY
    assert history == [(0, "user", "안녕하세요"), (1, "persona", FALLBACK_REPLY)]


def test_llm_failure_mid_reply_keeps_my_message_and_drops_half_reply():
    async def scenario(factory):
        sid = (await _start(factory)).session_id
        events = await _stream(factory, sid, FakePartner(fail_after=1), message="안녕하세요")
        return events, await _history(factory, sid)

    events, history = _run(scenario)

    assert [name for name, _ in events] == ["start", "delta", "error"]
    assert events[-1][1]["detail"].startswith("답변 도중 연결이 끊겼어요")
    assert history == [(0, "user", "안녕하세요")]  # 새로고침해도 내 메시지는 남고, 반 토막 답변은 없다


def test_retry_answers_my_unanswered_message_without_resending_it():
    async def scenario(factory):
        sid = (await _start(factory)).session_id
        await _stream(factory, sid, FakePartner(fail_after=1), message="안녕하세요")
        agent = FakePartner(chunks=("반가워요",))
        events = await _stream(factory, sid, agent, retry=True)
        return agent.calls, events, await _history(factory, sid)

    calls, events, history = _run(scenario)

    assert calls[0]["history"] == [{"role": "user", "content": "안녕하세요"}]
    assert events[-1] == ("done", {**events[-1][1], "message_index": 1, "content": "반가워요", "source": "llm"})
    assert history == [(0, "user", "안녕하세요"), (1, "persona", "반가워요")]


@pytest.mark.parametrize("already_answered", [False, True], ids=["empty-session", "last-is-persona"])
def test_retry_without_unanswered_message_is_refused(already_answered):
    async def scenario(factory):
        sid = (await _start(factory)).session_id
        if already_answered:
            await _stream(factory, sid, FakePartner(), message="안녕하세요")
        agent = FakePartner()
        try:
            await _stream(factory, sid, agent, retry=True)
        finally:
            assert agent.calls == []  # LLM 을 부르지 않는다

    with pytest.raises(NothingToRetry):
        _run(scenario)


def test_ended_session_refuses_to_stream():
    async def scenario(factory):
        sid = (await _start(factory)).session_id
        async with factory() as db:
            service = PracticeService(db)
            await service.end(await service.repo.get_session(sid))
            await db.commit()
        return await _stream(factory, sid, FakePartner(), message="안녕하세요")

    with pytest.raises(SessionEnded):
        _run(scenario)


# ── 먼저 인사하기 ─────────────────────────────────────


def test_opening_on_empty_session_asks_partner_to_greet_first():
    async def scenario(factory):
        sid = (await _start(factory)).session_id
        agent = FakePartner(chunks=("반가워요!",))
        await _stream(factory, sid, agent)
        return agent.calls, await _history(factory, sid)

    calls, history = _run(scenario)

    assert calls == [{"history": [], "opening": True}]
    assert history == [(0, "persona", "반가워요!")]


def test_history_after_partner_greeted_starts_with_opening_instruction():
    async def scenario(factory):
        sid = (await _start(factory)).session_id
        await _stream(factory, sid, FakePartner(chunks=("반가워요!",)))
        agent = FakePartner()
        await _stream(factory, sid, agent, message="저도요")
        return agent.calls[0]

    call = _run(scenario)

    assert call["opening"] is False
    assert call["history"] == [
        {"role": "user", "content": OPENING_INSTRUCTION},
        {"role": "assistant", "content": "반가워요!"},
        {"role": "user", "content": "저도요"},
    ]
