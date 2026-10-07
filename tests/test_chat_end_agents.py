import asyncio
import json

import pytest

from app.features.chat_end import agents
from app.features.chat_end.agents import (
    FALLBACK_DRAFTS,
    DraftAgent,
    EndMessageAgent,
    LLMError,
    fill_drafts,
)
from app.features.chat_end.schemas import EndType, RecentMessage, Speaker

RECENT = [
    RecentMessage(speaker=Speaker.REQUESTER, content="요즘 바쁘신가 봐요."),
    RecentMessage(speaker=Speaker.TARGET, content="네 좀 정신없었어요. 아쉽네요."),
]


def _fake_call(monkeypatch, replies):
    """_call(LLM 경계)만 가짜로. replies 를 차례로 돌려주고, Exception 이면 던진다."""
    calls = []
    queue = list(replies)

    async def fake_call(**kwargs):
        calls.append(kwargs)
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(agents, "_call", fake_call)
    return calls


def test_fill_drafts_normalizes_dedupes_and_takes_three():
    drafts, source = fill_drafts(
        [" 고마웠어요.  잘 지내세요 ", "고마웠어요. 잘 지내세요", "", 1, "b", "c", "d"], EndType.GENTLE
    )
    assert drafts == ["고마웠어요. 잘 지내세요", "b", "c"]
    assert source == "llm"


def test_fill_drafts_fills_missing_with_fallback():
    drafts, source = fill_drafts(["하나뿐"], EndType.DIRECT)
    assert drafts == ["하나뿐", *FALLBACK_DRAFTS[EndType.DIRECT][:2]]
    assert source == "fallback"


def test_fill_drafts_drops_too_long_items():
    drafts, source = fill_drafts(["가" * 301, "b", "c"], EndType.CASUAL)
    assert drafts == ["b", "c", FALLBACK_DRAFTS[EndType.CASUAL][0]]
    assert source == "fallback"


@pytest.mark.parametrize("end_type", list(EndType))
def test_fallback_drafts_are_three_unique_per_type(end_type):
    assert len(set(FALLBACK_DRAFTS[end_type])) == 3


def test_draft_agent_uses_llm_and_quotes_conversation(monkeypatch):
    calls = _fake_call(monkeypatch, [json.dumps({"endings": ["a", "b", "c"]}, ensure_ascii=False)])
    drafts, source = asyncio.run(DraftAgent().generate(end_type=EndType.GENTLE, recent=RECENT))
    assert (drafts, source) == (["a", "b", "c"], "llm")
    content = calls[0]["messages"][0]["content"]
    assert "<대화>" in content and "상대: 네 좀 정신없었어요. 아쉽네요." in content


@pytest.mark.parametrize("reply", ["not json", LLMError("timeout"), json.dumps({"endings": "x"})])
def test_draft_agent_falls_back(monkeypatch, reply):
    _fake_call(monkeypatch, [reply])
    drafts, source = asyncio.run(DraftAgent().generate(end_type=EndType.CASUAL, recent=RECENT))
    assert drafts == FALLBACK_DRAFTS[EndType.CASUAL]
    assert source == "fallback"


def test_end_message_agent_parses_and_includes_drafts_block(monkeypatch):
    calls = _fake_call(
        monkeypatch, ['```json\n{"ai_response": " 아쉽지만 고마웠어요. ", "end_reason": "상호 합의 종료"}\n```']
    )
    result = asyncio.run(
        EndMessageAgent().generate(end_type=EndType.GENTLE, recent=RECENT, endings=["고마웠어요. 좋은 인연 되세요."])
    )
    assert result.ai_response == "아쉽지만 고마웠어요."
    assert result.end_reason == "상호 합의 종료"
    content = calls[0]["messages"][0]["content"]
    assert "<초안>" in content and "고마웠어요. 좋은 인연 되세요." in content


def test_end_message_agent_regenerates_once_when_copying_draft(monkeypatch):
    ending = "고마웠어요. 좋은 인연 되세요."
    calls = _fake_call(
        monkeypatch,
        [
            json.dumps({"ai_response": ending, "end_reason": "r"}, ensure_ascii=False),
            json.dumps({"ai_response": "바쁘셨을 텐데 고마웠어요.", "end_reason": "r"}, ensure_ascii=False),
        ],
    )
    result = asyncio.run(EndMessageAgent().generate(end_type=EndType.GENTLE, recent=RECENT, endings=[ending]))
    assert result.ai_response == "바쁘셨을 텐데 고마웠어요."
    assert len(calls) == 2


def test_end_message_agent_accepts_copy_after_one_retry(monkeypatch):
    ending = "고마웠어요."
    copy = json.dumps({"ai_response": ending, "end_reason": "r"}, ensure_ascii=False)
    calls = _fake_call(monkeypatch, [copy, copy])
    result = asyncio.run(EndMessageAgent().generate(end_type=EndType.GENTLE, recent=RECENT, endings=[ending]))
    assert result.ai_response == ending
    assert len(calls) == 2


def test_end_message_agent_truncates_reason_and_allows_missing(monkeypatch):
    _fake_call(
        monkeypatch,
        [
            json.dumps({"ai_response": "a", "end_reason": "가" * 300}, ensure_ascii=False),
            json.dumps({"ai_response": "b"}, ensure_ascii=False),
        ],
    )
    first = asyncio.run(EndMessageAgent().generate(end_type=EndType.DIRECT, recent=RECENT, endings=["x"]))
    second = asyncio.run(EndMessageAgent().generate(end_type=EndType.DIRECT, recent=RECENT, endings=["x"]))
    assert len(first.end_reason) == 255
    assert second.end_reason is None


@pytest.mark.parametrize("reply", [json.dumps({"end_reason": "r"}), json.dumps({"ai_response": "  "}), "[]"])
def test_end_message_agent_raises_on_invalid_output(monkeypatch, reply):
    _fake_call(monkeypatch, [reply])
    with pytest.raises(LLMError):
        asyncio.run(EndMessageAgent().generate(end_type=EndType.GENTLE, recent=RECENT, endings=["x"]))


def test_notice_is_appended_to_prompt(monkeypatch):
    calls = _fake_call(monkeypatch, [json.dumps({"ai_response": "새 문장"}, ensure_ascii=False)])
    asyncio.run(
        EndMessageAgent().generate(end_type=EndType.GENTLE, recent=RECENT, endings=["x"], notice="\n[교정] 다시")
    )
    assert calls[0]["messages"][0]["content"].endswith("\n[교정] 다시")
