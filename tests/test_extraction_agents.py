"""StyleAgent — LLM 경계(_call_json)만 가짜로."""

import asyncio

import pytest

from app.features.persona.schemas import ConversationStyle
from app.features.persona_extraction import agents as agents_mod
from app.features.persona_extraction.agents import ExtractionFailed, StyleAgent


def test_style_agent_sends_previous_style_and_utterances(monkeypatch):
    seen = {}

    async def fake_call_json(**kw):
        seen.update(kw)
        return {"style": {"speech_level": "반말", "frequent_phrases": ["오 대박"]}, "positivity": 80}

    monkeypatch.setattr(agents_mod, "_call_json", fake_call_json)
    previous = ConversationStyle(speech_level="존댓말", frequent_phrases=["아하"])

    out = asyncio.run(StyleAgent().analyze(["오 대박 진짜?", "ㅋㅋㅋ 나도"], previous=previous))

    body = seen["messages"][0]["content"]
    assert "아하" in body and "오 대박 진짜?" in body
    assert out.style.speech_level == "반말"
    assert out.positivity == 80


def test_style_agent_wraps_llm_error(monkeypatch):
    async def boom(**kw):
        raise agents_mod.LLMError("timeout")

    monkeypatch.setattr(agents_mod, "_call_json", boom)

    with pytest.raises(ExtractionFailed):
        asyncio.run(StyleAgent().analyze(["안녕"], previous=None))


def test_style_agent_wraps_non_object_style(monkeypatch):
    async def weird(**kw):
        return {"style": "반말"}

    monkeypatch.setattr(agents_mod, "_call_json", weird)

    with pytest.raises(ExtractionFailed):
        asyncio.run(StyleAgent().analyze(["안녕"], previous=None))
