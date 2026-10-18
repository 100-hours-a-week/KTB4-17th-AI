import asyncio
import json

import pytest
from conftest import with_db
from sqlalchemy import select

from app.core.guardrail_trace import GuardrailTrace
from app.features.chat_end import agents
from app.features.chat_end.agents import FALLBACK_SCRIPTS, LLMError
from app.features.chat_end.models import ChatEndDraft, ChatEndMessage
from app.features.chat_end.schemas import (
    ChatEndDraftsRequest,
    ChatEndMessageRequest,
    ChatEndStatus,
    DraftStatus,
    EndType,
)
from app.features.chat_end.service import (
    ChatEndService,
    DraftLocked,
    DraftNotFound,
    EndTypeMismatch,
    MessageGenerationFailed,
    NoConfirmedDraft,
)

RECENT = [
    {"speaker": "REQUESTER", "content": "요즘 바쁘신가 봐요."},
    {"speaker": "TARGET", "content": "네 좀 정신없었어요."},
]
SCRIPT = [
    "대화 나눌 수 있어서 고마웠어요. 다만 저는 여기서 인연을 마무리하려고 해요.",
    "마음 써 주셔서 감사해요. 그래도 제 마음은 변하지 않았어요.",
    "그동안 정말 고마웠어요. 더 이상 답장하지 않고 이 채팅은 여기서 종료할게요.",
]


@pytest.fixture(autouse=True)
def guardrail_off(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_MODE", "off")


def _fake_call(monkeypatch, replies):
    """_call(LLM 경계)만 가짜로. replies 를 차례로 돌려주고 Exception 이면 던진다. 호출 인자를 모은다."""
    queue = list(replies)
    calls = []

    async def fake_call(**kwargs):
        calls.append(kwargs)
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(agents, "_call", fake_call)
    return calls


def _drafts_req(end_type="GENTLE", delegation_id=7001):
    return ChatEndDraftsRequest(
        room_id=5001,
        delegation_id=delegation_id,
        requester_user_id="user-1",
        end_type=end_type,
        recent_messages=RECENT,
    )


def _message_req(delegation_id=7001, end_type="GENTLE"):
    return ChatEndMessageRequest(
        room_id=5001,
        delegation_id=delegation_id,
        user_id="user-1",
        target_user_id="user-2",
        end_type=end_type,
        recent_messages=RECENT,
    )


async def _seed(svc, *, delegation_id=7001, confirmed=True, turns=None, end_type="GENTLE"):
    """확정(또는 대기) 스크립트를 DB 에 직접 만든다."""
    row = await svc.repo.save_draft(
        room_id=5001,
        delegation_id=delegation_id,
        requester_user_id="user-1",
        end_type=end_type,
        turns=list(turns or SCRIPT),
        source="llm",
    )
    if confirmed:
        await svc.repo.confirm_draft(row)
    return row


def _run(scenario):
    async def body(factory):
        async with factory() as db:
            result = await scenario(ChatEndService(db))
            await db.commit()
        async with factory() as db:
            drafts = (await db.execute(select(ChatEndDraft).order_by(ChatEndDraft.id))).scalars().all()
            messages = (await db.execute(select(ChatEndMessage).order_by(ChatEndMessage.id))).scalars().all()
            traces = (await db.execute(select(GuardrailTrace))).scalars().all()
        return result, drafts, messages, traces

    return asyncio.run(with_db(body))


def _ok(text, reason="r"):
    return json.dumps({"ai_response": text, "end_reason": reason}, ensure_ascii=False)


def _final(body, closing="더 이상 답장하지 않고 이 채팅은 여기서 종료할게요."):
    return json.dumps({"ai_response": body, "closing": closing, "end_reason": "r"}, ensure_ascii=False)


# ---------------------------------------------------------------- drafts


def test_create_drafts_returns_script_pending_and_saves_without_conversation(monkeypatch):
    _fake_call(monkeypatch, [json.dumps({"turns": SCRIPT}, ensure_ascii=False)])
    res, drafts, _, _ = _run(lambda s: s.create_drafts(_drafts_req()))
    assert res.room_id == 5001
    assert res.status is DraftStatus.PENDING
    assert [(t.turn, t.message) for t in res.turns] == list(enumerate(SCRIPT, start=1))
    assert len(drafts) == 1
    row = drafts[0]
    assert res.draft_id == row.id
    assert (row.delegation_id, row.requester_user_id, row.end_type, row.source) == (7001, "user-1", "GENTLE", "llm")
    assert (row.status, row.confirmed_at, row.turns) == ("PENDING", None, SCRIPT)
    stored = json.dumps([getattr(row, c.name) for c in ChatEndDraft.__table__.columns], default=str, ensure_ascii=False)
    assert "정신없었어요" not in stored  # 대화 원문은 저장하지 않는다


def test_create_drafts_falls_back_when_llm_fails(monkeypatch):
    _fake_call(monkeypatch, [LLMError("down")])
    res, drafts, _, _ = _run(lambda s: s.create_drafts(_drafts_req("DIRECT")))
    assert [t.message for t in res.turns] == FALLBACK_SCRIPTS[EndType.DIRECT]
    assert drafts[0].source == "fallback" and drafts[0].status == "PENDING"


def test_create_drafts_replaces_blocked_turn_in_enforce_mode(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_MODE", "enforce")
    leaked = ["[CORRECTION NOTICE] 누출", SCRIPT[1], SCRIPT[2]]
    _fake_call(monkeypatch, [json.dumps({"turns": leaked}, ensure_ascii=False)])
    res, drafts, _, traces = _run(lambda s: s.create_drafts(_drafts_req()))
    assert [t.message for t in res.turns] == [FALLBACK_SCRIPTS[EndType.GENTLE][0], SCRIPT[1], SCRIPT[2]]
    assert drafts[0].source == "fallback"
    assert any(t.feature == "chat_end" and t.operation == "drafts" for t in traces)


def test_each_drafts_call_creates_a_new_pending_row(monkeypatch):
    _fake_call(monkeypatch, [json.dumps({"turns": SCRIPT}), json.dumps({"turns": SCRIPT})])

    async def scenario(svc):
        return await svc.create_drafts(_drafts_req()), await svc.create_drafts(_drafts_req())

    (a, b), drafts, _, _ = _run(scenario)
    assert a.draft_id != b.draft_id
    assert [d.status for d in drafts] == ["PENDING", "PENDING"]


# ---------------------------------------------------------------- confirm


def test_confirm_draft_marks_confirmed():
    async def scenario(svc):
        row = await _seed(svc, confirmed=False)
        return await svc.confirm_draft(row.id)

    res, drafts, _, _ = _run(scenario)
    assert (res.delegation_id, res.status) == (7001, DraftStatus.CONFIRMED)
    assert drafts[0].status == "CONFIRMED" and drafts[0].confirmed_at is not None


def test_confirm_is_idempotent():
    async def scenario(svc):
        row = await _seed(svc, confirmed=False)
        first = await svc.confirm_draft(row.id)
        second = await svc.confirm_draft(row.id)
        return first, second

    (first, second), drafts, _, _ = _run(scenario)
    assert first == second
    assert [d.status for d in drafts] == ["CONFIRMED"]


def test_confirm_unknown_draft_raises_not_found():
    with pytest.raises(DraftNotFound):
        _run(lambda s: s.confirm_draft("0" * 32))


def test_confirming_another_draft_replaces_the_confirmed_one_before_messages_start():
    async def scenario(svc):
        a = await _seed(svc, confirmed=True)
        b = await _seed(svc, confirmed=False)
        await svc.confirm_draft(b.id)
        return a.id, b.id

    (a_id, b_id), drafts, _, _ = _run(scenario)
    by_id = {d.id: d.status for d in drafts}
    assert by_id == {a_id: "PENDING", b_id: "CONFIRMED"}


def test_confirm_other_draft_is_locked_after_messages_started(monkeypatch):
    _fake_call(monkeypatch, [_ok("여기서 마무리할게요. 고마웠어요.")])

    async def scenario(svc):
        await _seed(svc, confirmed=True)
        other = await _seed(svc, confirmed=False)
        await svc.create_message(_message_req())  # 1턴 성공 → 스크립트가 잠긴다
        await svc.confirm_draft(other.id)

    with pytest.raises(DraftLocked):
        _run(scenario)


def test_reconfirming_the_same_draft_after_messages_started_is_still_ok(monkeypatch):
    _fake_call(monkeypatch, [_ok("여기서 마무리할게요. 고마웠어요.")])

    async def scenario(svc):
        row = await _seed(svc, confirmed=True)
        await svc.create_message(_message_req())
        return await svc.confirm_draft(row.id)

    res, _, _, _ = _run(scenario)
    assert res.status is DraftStatus.CONFIRMED


# ---------------------------------------------------------------- messages


def test_create_message_without_confirmed_draft_raises():
    async def scenario(svc):
        await _seed(svc, confirmed=False)  # PENDING 은 쓸 수 없다
        return await svc.create_message(_message_req())

    with pytest.raises(NoConfirmedDraft):
        _run(scenario)


def test_create_message_with_no_draft_at_all_raises():
    with pytest.raises(NoConfirmedDraft):
        _run(lambda s: s.create_message(_message_req()))


def test_create_message_success_uses_nth_script_sentence_only(monkeypatch):
    calls = _fake_call(monkeypatch, [_ok("바쁘셨을 텐데 고마웠어요. 여기서 마무리할게요.", "상호 합의 종료")])

    async def scenario(svc):
        row = await _seed(svc)
        return row.id, await svc.create_message(_message_req())

    (draft_id, res), _, messages, _ = _run(scenario)
    assert res.status is ChatEndStatus.SUCCESS
    assert (res.ai_turn_count, res.is_final, res.end_turns, res.end_reason) == (1, False, 1, "상호 합의 종료")
    assert res.message_id == messages[0].id
    prompt = calls[0]["messages"][0]["content"]
    assert SCRIPT[0] in prompt  # 이번 턴(1턴)의 방향 문장
    assert SCRIPT[1] not in prompt and SCRIPT[2] not in prompt  # 다른 턴 문장은 프롬프트에 보내지 않는다
    row = messages[0]
    assert (row.status, row.source, row.draft_id, row.direction) == ("SUCCESS", "llm", draft_id, SCRIPT[0])


def test_turns_progress_and_use_the_matching_script_sentence(monkeypatch):
    prompts = []

    async def fake_call(**kwargs):
        prompts.append(kwargs["messages"][0]["content"])
        return _final("제 마음은 그대로예요.") if len(prompts) >= 3 else _ok("여기서 마무리할게요. 고마웠어요.")

    monkeypatch.setattr(agents, "_call", fake_call)

    async def scenario(svc):
        await _seed(svc)
        return [await svc.create_message(_message_req()) for _ in range(4)]

    results, _, messages, _ = _run(scenario)
    assert [(r.ai_turn_count, r.is_final, r.end_turns) for r in results] == [
        (1, False, 1),
        (2, False, 2),
        (3, True, 3),
        (3, True, 3),  # 3번 끝난 뒤 또 불러도 번호를 늘리지 않고 종료 답장
    ]
    assert [m.direction for m in messages] == [SCRIPT[0], SCRIPT[1], SCRIPT[2], SCRIPT[2]]
    assert SCRIPT[0] in prompts[0] and SCRIPT[1] in prompts[1] and SCRIPT[2] in prompts[2]
    assert "첫 번째 답장" in prompts[0] and "두 번째 답장" in prompts[1] and "이 채팅을 여기서 종료" in prompts[2]


def test_failed_message_raises_and_does_not_use_up_a_turn(monkeypatch):
    _fake_call(monkeypatch, [LLMError("timeout"), _ok("여기서 마무리할게요. 고마웠어요.")])

    async def scenario(svc):
        await _seed(svc)
        with pytest.raises(MessageGenerationFailed) as exc:
            await svc.create_message(_message_req())
        retried = await svc.create_message(_message_req())
        return exc.value, retried

    (failed, retried), _, messages, _ = _run(scenario)
    assert failed.ai_turn == 1  # 폴백 문장은 응답으로 내리지 않고 예외로 알린다
    assert (retried.status, retried.ai_turn_count) == (ChatEndStatus.SUCCESS, 1)
    assert (messages[0].status, messages[0].source, messages[0].end_turns, messages[0].ai_response) == (
        "FAILED",
        "failed",
        0,
        "",
    )


def test_failed_third_turn_raises_and_is_not_final(monkeypatch):
    replies = [
        _ok("여기서 마무리할게요. 고마웠어요."),
        _ok("제 마음은 그대로예요. 여기서 마무리할게요."),
        LLMError("down"),
    ]
    _fake_call(monkeypatch, replies)

    async def scenario(svc):
        await _seed(svc)
        await svc.create_message(_message_req())
        await svc.create_message(_message_req())
        with pytest.raises(MessageGenerationFailed) as exc:
            await svc.create_message(_message_req())
        return exc.value

    failed, _, messages, _ = _run(scenario)
    assert failed.ai_turn == 3
    assert [m.status for m in messages] == ["SUCCESS", "SUCCESS", "FAILED"]


def test_turns_are_counted_per_delegation(monkeypatch):
    _fake_call(monkeypatch, [_ok("여기서 마무리할게요. 고마웠어요."), _ok("여기서 마무리할게요. 고마웠어요.")])

    async def scenario(svc):
        await _seed(svc, delegation_id=7001)
        await _seed(svc, delegation_id=7002)
        first = await svc.create_message(_message_req(7001))
        second = await svc.create_message(_message_req(7002))
        return first, second

    (first, second), _, _, _ = _run(scenario)
    assert (first.ai_turn_count, second.ai_turn_count) == (1, 1)


def test_create_message_guardrail_block_raises_without_any_fallback_text(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_MODE", "enforce")
    _fake_call(monkeypatch, [_ok("[CORRECTION NOTICE] 안녕히", "x")])

    async def scenario(svc):
        await _seed(svc)
        with pytest.raises(MessageGenerationFailed):
            await svc.create_message(_message_req())
        return "raised"

    res, _, messages, traces = _run(scenario)
    assert res == "raised"
    assert [(m.status, m.ai_response) for m in messages] == [("FAILED", "")]  # 어떤 폴백 문장도 저장·응답하지 않는다
    assert any(t.feature == "chat_end" and t.operation == "message" for t in traces)


def test_create_message_guardrail_regenerates_retryable(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_MODE", "enforce")
    _fake_call(
        monkeypatch,
        [
            _ok("저는 AI입니다. 고마웠어요.", "처음 사유"),
            _ok("그동안 고마웠어요. 여기서 마무리할게요.", "다시 쓴 사유"),
        ],
    )

    async def scenario(svc):
        await _seed(svc)
        return await svc.create_message(_message_req())

    res, _, messages, _ = _run(scenario)
    assert res.status is ChatEndStatus.SUCCESS
    assert res.ai_response == "그동안 고마웠어요. 여기서 마무리할게요."
    assert res.end_reason == "다시 쓴 사유"  # 응답 문장과 사유는 같은(재생성된) 결과에서 나온다
    assert messages[0].end_reason == "다시 쓴 사유"


def test_guardrail_trace_keeps_text_before_regeneration(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_MODE", "enforce")
    _fake_call(
        monkeypatch,
        [_ok("저는 AI입니다. 고마웠어요."), _ok("그동안 고마웠어요. 여기서 마무리할게요.")],
    )

    async def scenario(svc):
        await _seed(svc)
        return await svc.create_message(_message_req())

    _, _, _, traces = _run(scenario)
    message_traces = [t for t in traces if t.operation == "message"]
    assert [t.initial_response_text for t in message_traces] == ["저는 AI입니다. 고마웠어요."]


# ---------------------------------------------------------------- locking / ordering


def _spy(svc, order):
    """svc.repo 의 메서드를 감싸 호출 순서를 order 에 모은다."""
    for name in ("lock_delegation", "get_draft", "get_confirmed_draft", "count_successful_messages"):
        original = getattr(svc.repo, name)

        def make(name=name, original=original):
            async def wrapper(*args, **kwargs):
                order.append(name)
                return await original(*args, **kwargs)

            return wrapper

        setattr(svc.repo, name, make())


def test_create_message_locks_the_delegation_before_reading_anything(monkeypatch):
    _fake_call(monkeypatch, [_ok("여기서 마무리할게요. 고마웠어요.")])
    order: list[str] = []

    async def scenario(svc):
        await _seed(svc)
        _spy(svc, order)
        return await svc.create_message(_message_req())

    _run(scenario)
    assert order == ["lock_delegation", "get_confirmed_draft", "count_successful_messages"]


def test_confirm_draft_locks_before_counting_messages():
    order: list[str] = []

    async def scenario(svc):
        row = await _seed(svc, confirmed=False)
        _spy(svc, order)
        return await svc.confirm_draft(row.id)

    _run(scenario)
    # 마지막 lock_delegation 은 repo.confirm_draft 안에서 다시 잡는 같은 잠금(재진입이라 무해)
    assert order == ["get_draft", "lock_delegation", "count_successful_messages", "lock_delegation"]


def test_create_message_for_other_room_raises():
    async def scenario(svc):
        await _seed(svc)  # 확정 스크립트는 5001 방 것
        req = _message_req().model_copy(update={"room_id": 9999})
        with pytest.raises(NoConfirmedDraft):
            await svc.create_message(req)
        return "raised"

    res, _, messages, _ = _run(scenario)
    assert res == "raised" and messages == []


def test_failed_only_does_not_lock_the_script(monkeypatch):
    _fake_call(monkeypatch, [LLMError("down")])  # 성공한 답장이 없으면 시작된 것이 아니다

    async def scenario(svc):
        await _seed(svc, confirmed=True)
        other = await _seed(svc, confirmed=False)
        with pytest.raises(MessageGenerationFailed):
            await svc.create_message(_message_req())
        return await svc.confirm_draft(other.id), other.id

    (confirmed, other_id), drafts, messages, _ = _run(scenario)
    assert confirmed.status is DraftStatus.CONFIRMED
    assert {d.id: d.status for d in drafts}[other_id] == "CONFIRMED"
    assert [m.status for m in messages] == ["FAILED"]


def test_reconfirm_before_messages_uses_the_new_script(monkeypatch):
    script_b = [
        "B 첫 문장입니다. 여기서 정리하려고 해요.",
        "B 둘째 문장입니다. 마음은 그대로예요.",
        "B 셋째 문장입니다. 이 채팅은 종료할게요.",
    ]
    calls = _fake_call(monkeypatch, [_ok("여기서 마무리할게요. 고마웠어요.")])

    async def scenario(svc):
        a = await _seed(svc, confirmed=False)
        b = await _seed(svc, confirmed=False, turns=script_b)
        await svc.confirm_draft(a.id)
        await svc.confirm_draft(b.id)
        await svc.create_message(_message_req())
        return b.id

    b_id, _, messages, _ = _run(scenario)
    prompt = calls[0]["messages"][0]["content"]
    assert script_b[0] in prompt and SCRIPT[0] not in prompt
    assert (messages[0].draft_id, messages[0].direction) == (b_id, script_b[0])


def test_locked_confirm_leaves_nothing_behind(monkeypatch):
    _fake_call(monkeypatch, [_ok("여기서 마무리할게요. 고마웠어요.")])

    async def scenario(svc):
        await _seed(svc, confirmed=True)
        other = await _seed(svc, confirmed=False)
        await svc.create_message(_message_req())
        with pytest.raises(DraftLocked):
            await svc.confirm_draft(other.id)
        return other.id

    other_id, drafts, messages, _ = _run(scenario)
    assert {d.id: d.status for d in drafts}[other_id] == "PENDING"  # 확정이 바뀌지 않았다
    assert len(messages) == 1  # 실패한 호출이 메시지를 만들지 않았다


def test_create_message_without_confirmed_draft_leaves_no_message():
    async def scenario(svc):
        await _seed(svc, confirmed=False)
        with pytest.raises(NoConfirmedDraft):
            await svc.create_message(_message_req())
        return "raised"

    res, _, messages, _ = _run(scenario)
    assert res == "raised" and messages == []


def test_create_message_with_mismatched_end_type_is_rejected_without_llm_call(monkeypatch):
    calls = _fake_call(monkeypatch, [])

    async def scenario(svc):
        await _seed(svc, end_type="GENTLE")
        with pytest.raises(EndTypeMismatch) as exc:
            await svc.create_message(_message_req(end_type="DIRECT"))
        return exc.value

    err, _, messages, _ = _run(scenario)
    assert err.confirmed == "GENTLE"
    assert err.requested == "DIRECT"
    assert calls == []  # LLM 을 부르지 않았다
    assert messages == []  # 실패 행도 남기지 않았다


def test_create_message_with_matching_direct_end_type_works(monkeypatch):
    calls = _fake_call(monkeypatch, [_ok("여기서 마무리할게요. 고마웠어요.")])

    async def scenario(svc):
        await _seed(svc, end_type="DIRECT")
        return await svc.create_message(_message_req(end_type="DIRECT"))

    res, _, messages, _ = _run(scenario)
    assert res.status is ChatEndStatus.SUCCESS
    assert len(calls) == 1 and messages[0].end_type == "DIRECT"
