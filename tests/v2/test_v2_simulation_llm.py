"""LLM 도구 호출 래퍼 — 가짜 OpenAI 클라이언트로 tools 경로와 json_object 대체 경로를 본다."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.v2.simulation import llm as llm_mod
from app.v2.simulation.generator import SUBMIT_SCRIPT_TOOL, parse_lines
from app.v2.simulation.llm import SimLLMError, call_tools


def _resp(*, tool_calls=None, content=None, finish="stop"):
    msg = SimpleNamespace(tool_calls=tool_calls, content=content)
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=20)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish)], usage=usage)


def _tc(name, args):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=args))


class FakeClient:
    def __init__(self, resp):
        self.resp = resp
        self.kwargs = None
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def with_options(self, **kw):
        self.options = kw
        return self

    async def _create(self, **kw):
        self.kwargs = kw
        if isinstance(self.resp, Exception):
            raise self.resp
        return self.resp


def _call(monkeypatch, resp, *, use_tools=True):
    client = FakeClient(resp)
    monkeypatch.setattr(llm_mod, "_get_client", lambda: client)
    monkeypatch.setattr(get_settings(), "simulation_v2_use_tools", use_tools)
    out = asyncio.run(call_tools(system="s", user="u", tools=[SUBMIT_SCRIPT_TOOL], max_tokens=100, timeout=5, name="t"))
    return out, client


def test_tools_path_parses_parallel_calls(monkeypatch):
    resp = _resp(tool_calls=[_tc("patch_line", '{"i": 1, "text": "a"}'), _tc("patch_line", "broken{")])
    out, client = _call(monkeypatch, resp)
    assert [c.arguments for c in out.calls] == [{"i": 1, "text": "a"}]
    assert client.kwargs["tool_choice"] == "required" and client.kwargs["tools"]
    assert client.options == {"max_retries": 1}
    assert out.prompt_tokens == 10 and out.completion_tokens == 20


def test_tools_path_without_calls_raises(monkeypatch):
    with pytest.raises(SimLLMError) as e:
        _call(monkeypatch, _resp(tool_calls=[], content="hi"))
    assert e.value.reason == "no_tool_call"


def test_truncated(monkeypatch):
    with pytest.raises(SimLLMError) as e:
        _call(monkeypatch, _resp(tool_calls=[_tc("x", "{}")], finish="length"))
    assert e.value.reason == "truncated"


def test_json_fallback_path(monkeypatch):
    content = '```json\n{"calls": [{"name": "submit_script", "arguments": {"lines": []}}]}\n```'
    out, client = _call(monkeypatch, _resp(content=content), use_tools=False)
    assert out.calls[0].name == "submit_script"
    assert client.kwargs["response_format"] == {"type": "json_object"}
    assert "tools" not in client.kwargs
    assert "submit_script" in client.kwargs["messages"][0]["content"]


def test_json_fallback_invalid(monkeypatch):
    with pytest.raises(SimLLMError) as e:
        _call(monkeypatch, _resp(content="nope"), use_tools=False)
    assert e.value.reason == "invalid_json"


def test_upstream_exception_wrapped(monkeypatch):
    with pytest.raises(SimLLMError):
        _call(monkeypatch, RuntimeError("down"))


def test_parse_lines_normalizes_speakers_and_reindexes():
    lines = parse_lines(
        [
            {"i": 5, "speaker": "민수", "text": " 안녕하세요 ", "fact_refs": ["A-f1", "A-f1"]},
            {"i": 6, "speaker": "누구", "text": "버림"},
            {"i": 7, "speaker": "b", "text": ""},
            {"i": 8, "speaker": "B", "text": "반가워요", "fact_refs": "A-f1"},
            "garbage",
        ],
        "민수",
        "지은",
    )
    assert [(x.i, x.speaker, x.text, x.fact_refs) for x in lines] == [
        (0, "a", "안녕하세요", ["A-f1"]),
        (1, "b", "반가워요", []),
    ]
    assert parse_lines(None, "a", "b") == []


def test_provider_error_salvages_parsed_calls_and_dedupes(monkeypatch):
    """실측: gemini 가 같은 도구를 반복 호출하다 MALFORMED_FUNCTION_CALL 로 끊김 → 온전한 호출은 살린다."""
    same = '{"issues": [], "pass": true}'
    resp = _resp(tool_calls=[_tc("submit_review", same), _tc("submit_review", same), _tc("x", "{bro")], finish="error")
    out, _ = _call(monkeypatch, resp)
    assert [c.name for c in out.calls] == ["submit_review"]


def test_provider_error_without_calls_raises(monkeypatch):
    with pytest.raises(SimLLMError) as e:
        _call(monkeypatch, _resp(tool_calls=None, finish="error"))
    assert e.value.reason == "upstream_error"


def test_script_tool_uses_nicknames_unless_same():
    from app.v2.simulation.generator import script_tool

    def enum(t):
        return t["function"]["parameters"]["properties"]["lines"]["items"]["properties"]["speaker"]["enum"]

    assert enum(script_tool("민수", "지은")) == ["민수", "지은"]
    assert enum(script_tool("민수", "민수")) == ["a", "b"]
    assert enum(SUBMIT_SCRIPT_TOOL) == ["a", "b"]  # 원본은 바뀌지 않는다


def test_normalize_lines_merges_trims_and_reindexes():
    from app.v2.simulation.generator import normalize_lines
    from app.v2.simulation.schemas import ScriptLineV2 as L

    raw = [
        L(i=0, speaker="b", text="먼저 말한 b"),
        L(i=1, speaker="a", text="안녕", fact_refs=["A-f1"]),
        L(i=2, speaker="a", text="하세요", fact_refs=["A-f2"]),
        L(i=3, speaker="b", text="반가워요"),
        L(i=4, speaker="a", text="넘침1"),
        L(i=5, speaker="b", text="넘침2"),
    ]
    out = normalize_lines(raw, 1)
    assert [(x.i, x.speaker, x.text, x.fact_refs) for x in out] == [
        (0, "a", "안녕 하세요", ["A-f1", "A-f2"]),
        (1, "b", "반가워요", []),
    ]


def test_parse_lines_moves_inline_fact_ids_out_of_text():
    """실측: 닉네임 화자 도구에서 모델이 '…가거든요. A-f1' 처럼 ID 를 본문에 씀."""
    lines = parse_lines(
        [
            {"speaker": "민수", "text": "저는 주말마다 산에 가거든요. A-f1", "fact_refs": []},
            {"speaker": "지은", "text": "저도 트레킹 해요 (B-f1) ㅎㅎ", "fact_refs": ["B-f1"]},
        ],
        "민수",
        "지은",
    )
    assert [(x.text, x.fact_refs) for x in lines] == [
        ("저는 주말마다 산에 가거든요.", ["A-f1"]),
        ("저도 트레킹 해요 ㅎㅎ", ["B-f1"]),
    ]


def test_parse_lines_literal_speaker_beats_nickname():
    lines = parse_lines([{"speaker": "b", "text": "안녕"}], "b", "지은")
    assert lines[0].speaker == "b"


def test_json_fallback_accepts_bare_arguments_for_single_tool(monkeypatch):
    """실측: 모델이 {"calls": [...]} 없이 인자 객체만 보냄 → 도구가 하나면 그 도구 인자로 본다."""
    out, _ = _call(monkeypatch, _resp(content='{"lines": []}'), use_tools=False)
    assert out.calls[0].name == "submit_script" and out.calls[0].arguments == {"lines": []}


def test_default_is_json_mode():
    from app.core.config import Settings

    assert Settings.model_fields["simulation_v2_use_tools"].default is False
