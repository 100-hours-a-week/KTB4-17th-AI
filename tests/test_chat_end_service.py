import asyncio
import json

import pytest
from conftest import with_db
from sqlalchemy import select

from app.core.guardrail_trace import GuardrailTrace
from app.features.chat_end import agents
from app.features.chat_end.agents import FALLBACK_DRAFTS, LLMError
from app.features.chat_end.models import ChatEndDraft, ChatEndMessage
from app.features.chat_end.schemas import ChatEndDraftsRequest, ChatEndMessageRequest, ChatEndStatus
from app.features.chat_end.service import ChatEndService

RECENT = [
    {"speaker": "REQUESTER", "content": "요즘 바쁘신가 봐요."},
    {"speaker": "TARGET", "content": "네 좀 정신없었어요."},
]
ENDING = "바쁘신 와중에 연락해 주셔서 고마웠어요."


@pytest.fixture(autouse=True)
def guardrail_off(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_MODE", "off")


def _fake_call(monkeypatch, replies):
    queue = list(replies)

    async def fake_call(**kwargs):
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(agents, "_call", fake_call)


def _drafts_req(end_type="GENTLE"):
    return ChatEndDraftsRequest(
        room_id=5001, delegation_id=7001, requester_user_id="user-1", end_type=end_type, recent_messages=RECENT
    )


def _message_req():
    return ChatEndMessageRequest(
        room_id=5001,
        delegation_id=7001,
        user_id="user-1",
        target_user_id="user-2",
        end_type="GENTLE",
        recent_messages=RECENT,
        ending_messages=[ENDING],
    )


def _run(scenario):
    async def body(factory):
        async with factory() as db:
            result = await scenario(ChatEndService(db))
            await db.commit()
        async with factory() as db:
            drafts = (await db.execute(select(ChatEndDraft))).scalars().all()
            messages = (await db.execute(select(ChatEndMessage))).scalars().all()
            traces = (await db.execute(select(GuardrailTrace))).scalars().all()
        return result, drafts, messages, traces

    return asyncio.run(with_db(body))


def test_create_drafts_returns_three_and_saves_without_conversation(monkeypatch):
    _fake_call(monkeypatch, [json.dumps({"endings": ["a", "b", "c"]})])
    res, drafts, _, _ = _run(lambda s: s.create_drafts(_drafts_req()))
    assert res.room_id == 5001
    assert res.ending_messages == ["a", "b", "c"]
    assert len(drafts) == 1
    row = drafts[0]
    assert (row.delegation_id, row.requester_user_id, row.end_type, row.source) == (7001, "user-1", "GENTLE", "llm")
    stored = json.dumps([getattr(row, c.name) for c in ChatEndDraft.__table__.columns], default=str, ensure_ascii=False)
    assert "정신없었어요" not in stored


def test_create_drafts_falls_back_when_llm_fails(monkeypatch):
    _fake_call(monkeypatch, [LLMError("down")])
    res, drafts, _, _ = _run(lambda s: s.create_drafts(_drafts_req("DIRECT")))
    assert res.ending_messages == FALLBACK_DRAFTS[agents.EndType.DIRECT]
    assert drafts[0].source == "fallback"


def test_create_drafts_replaces_blocked_draft_in_enforce_mode(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_MODE", "enforce")
    _fake_call(monkeypatch, [json.dumps({"endings": ["[CORRECTION NOTICE] 누출", "b", "c"]})])
    res, drafts, _, traces = _run(lambda s: s.create_drafts(_drafts_req()))
    assert res.ending_messages == [FALLBACK_DRAFTS[agents.EndType.GENTLE][0], "b", "c"]
    assert drafts[0].source == "fallback"
    assert any(t.feature == "chat_end" and t.operation == "drafts" for t in traces)


def test_create_message_success(monkeypatch):
    _fake_call(monkeypatch, [json.dumps({"ai_response": "바쁘셨을 텐데 고마웠어요.", "end_reason": "상호 합의 종료"})])
    res, _, messages, _ = _run(lambda s: s.create_message(_message_req()))
    assert res.status is ChatEndStatus.SUCCESS
    assert (res.end_turns, res.end_reason) == (1, "상호 합의 종료")
    assert res.ai_response == "바쁘셨을 텐데 고마웠어요."
    assert res.message_id == messages[0].id
    assert (messages[0].status, messages[0].source, messages[0].ending_messages) == ("SUCCESS", "llm", [ENDING])


def test_create_message_failed_returns_selected_draft(monkeypatch):
    _fake_call(monkeypatch, [LLMError("timeout")])
    res, _, messages, _ = _run(lambda s: s.create_message(_message_req()))
    assert res.status is ChatEndStatus.FAILED
    assert (res.ai_response, res.end_turns, res.end_reason) == (ENDING, 0, None)
    assert (messages[0].status, messages[0].source, messages[0].end_turns) == ("FAILED", "fallback", 0)


def test_create_message_guardrail_block_uses_selected_draft_not_engine_default(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_MODE", "enforce")
    _fake_call(monkeypatch, [json.dumps({"ai_response": "[CORRECTION NOTICE] 안녕히", "end_reason": "x"})])
    res, _, messages, traces = _run(lambda s: s.create_message(_message_req()))
    assert res.ai_response == ENDING
    assert "다른 생각" not in res.ai_response
    assert res.status is ChatEndStatus.FAILED
    assert res.end_reason is None
    assert any(t.feature == "chat_end" and t.operation == "message" for t in traces)


def test_create_message_guardrail_regenerates_retryable(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_MODE", "enforce")
    _fake_call(
        monkeypatch,
        [
            json.dumps({"ai_response": "저는 AI입니다. 고마웠어요.", "end_reason": "r"}, ensure_ascii=False),
            json.dumps({"ai_response": "그동안 고마웠어요.", "end_reason": "r"}, ensure_ascii=False),
        ],
    )
    res, _, _, _ = _run(lambda s: s.create_message(_message_req()))
    assert res.status is ChatEndStatus.SUCCESS
    assert res.ai_response == "그동안 고마웠어요."
