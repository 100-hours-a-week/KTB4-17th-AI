import asyncio

import pytest
from conftest import seed_persona, with_db

from app.features.practice.agents import FALLBACK_REPLY, OPENING_INSTRUCTION, LLMError, PartnerAgent
from app.features.practice.schemas import PracticeStartRequest
from app.features.practice.service import (
    ConcurrentRequest,
    NothingToRetry,
    OpeningAlreadyDone,
    PersonaNotFound,
    PracticeService,
    ReplyFailed,
    SessionEnded,
    collect_reply,
)


class FakePartner(PartnerAgent):
    """LLM 자리만 바꾼 상대 에이전트. chunks 를 차례로 내고, fail_after 번째 조각 뒤에 LLMError."""

    def __init__(self, chunks=("안녕", "하세요"), fail_after=None):
        self.chunks = chunks
        self.fail_after = fail_after
        self.calls = []
        self.trace_metadata = []

    async def reply(self, *, system, history, opening=False, trace_metadata=None):
        self.calls.append({"history": history, "opening": opening})
        self.trace_metadata.append(trace_metadata)
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
        result = await service.start(PracticeStartRequest(partner_user_id="u-partner", **req))
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
        return await _start(factory, me_user_id="u-me")

    assert _run(scenario).my_nickname == "민수"


def test_start_with_unconfirmed_partner_is_persona_not_found():
    async def scenario(factory):
        async with factory() as db:
            await PracticeService(db).start(PracticeStartRequest(partner_user_id="u-draft"))

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


def test_reply_passes_user_session_and_message_metadata_to_langfuse():
    async def scenario(factory):
        sid = (await _start(factory, me_user_id="u-me")).session_id
        agent = FakePartner(chunks=("반가워요",))
        await _stream(factory, sid, agent, message="안녕하세요")
        return sid, agent.trace_metadata[0]

    sid, metadata = _run(scenario)

    assert metadata["feature"] == "practice"
    assert metadata["operation"] == "reply"
    assert metadata["langfuse_user_id"] == "u-me"
    assert metadata["langfuse_session_id"] == sid
    assert metadata["messageIndex"] == 1
    assert metadata["opening"] is False


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


def test_opening_twice_is_refused_without_calling_llm():
    async def scenario(factory):
        sid = (await _start(factory)).session_id
        await _stream(factory, sid, FakePartner(chunks=("반가워요!",)))
        agent = FakePartner()
        try:
            await _stream(factory, sid, agent)
        finally:
            assert agent.calls == []

    with pytest.raises(OpeningAlreadyDone):
        _run(scenario)


# ── 닉네임 ────────────────────────────────────────────


def test_nickname_is_ignored_when_my_persona_exists():
    async def scenario(factory):
        return await _start(factory, me_user_id="u-me", nickname="다른이름")

    assert _run(scenario).my_nickname == "민수"


def test_nickname_is_used_when_no_my_persona():
    async def scenario(factory):
        return await _start(factory, nickname="직접입력")

    assert _run(scenario).my_nickname == "직접입력"


# ── 동시 요청 ─────────────────────────────────────────


def test_concurrent_message_with_same_index_loses_and_is_not_saved():
    async def scenario(factory):
        sid = (await _start(factory)).session_id
        # 두 요청이 같은 시점(message_count=0)의 세션을 읽었다
        async with factory() as db_a, factory() as db_b:
            svc_a, svc_b = PracticeService(db_a), PracticeService(db_b)
            svc_a.agent, svc_b.agent = FakePartner(), FakePartner()
            session_a = await svc_a.repo.get_session(sid)
            session_b = await svc_b.repo.get_session(sid)
            await _collect(svc_a.stream_reply(session_a, "먼저 온 메시지"))
            with pytest.raises(ConcurrentRequest):
                await _collect(svc_b.stream_reply(session_b, "늦게 온 메시지"))
        return await _history(factory, sid)

    history = _run(scenario)

    assert [(i, r, c) for i, r, c in history if r == "user"] == [(0, "user", "먼저 온 메시지")]


# ── 목록 ──────────────────────────────────────────────


def test_list_returns_only_my_sessions_newest_first_with_limit():
    async def scenario(factory):
        first = (await _start(factory, me_user_id="u-me")).session_id
        second = (await _start(factory, me_user_id="u-me")).session_id
        await _start(factory)  # me 없이 시작 — 목록에 안 나온다
        async with factory() as db:
            service = PracticeService(db)
            return first, second, await service.list_for_user("u-me", 50), await service.list_for_user("u-me", 1)

    first, second, listed, limited = _run(scenario)

    assert {s.session_id for s in listed} == {first, second}
    assert all(s.partner.nickname == "지수" and s.status == "active" for s in listed)
    assert len(limited) == 1


def test_list_for_unknown_user_is_empty():
    async def scenario(factory):
        async with factory() as db:
            return await PracticeService(db).list_for_user("nobody", 50)

    assert _run(scenario) == []


# ── 일반(JSON)용 collect_reply ────────────────────────


def test_collect_reply_returns_final_done_event():
    async def scenario(factory):
        sid = (await _start(factory)).session_id
        async with factory() as db:
            service = PracticeService(db)
            service.agent = FakePartner(chunks=("안녕", "하세요"))
            session = await service.repo.get_session(sid)
            return await collect_reply(service.stream_opening(session))

    done = _run(scenario)

    assert done.content == "안녕하세요"
    assert (done.message_index, done.source) == (0, "llm")


def test_collect_reply_turns_mid_stream_break_into_reply_failed_and_keeps_my_message():
    async def scenario(factory):
        sid = (await _start(factory)).session_id
        async with factory() as db:
            service = PracticeService(db)
            service.agent = FakePartner(fail_after=1)
            session = await service.repo.get_session(sid)
            with pytest.raises(ReplyFailed):
                await collect_reply(service.stream_reply(session, "안녕하세요"))
        return await _history(factory, sid)

    assert _run(scenario) == [(0, "user", "안녕하세요")]  # 답변만 실패, 내 메시지는 남아 /retry 로 이어진다
