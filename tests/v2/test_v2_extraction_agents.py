"""ConversationAgent — LLM 경계(_call_json)만 가짜로."""

import asyncio

import pytest

from app.features.persona.schemas import ConversationStyle
from app.v2.persona_extraction import agents as agents_mod
from app.v2.persona_extraction.agents import ConversationAgent, ExtractionFailed
from app.v2.persona_extraction.schemas import V2Extraction


def test_agent_sends_previous_style_and_transcript_and_parses_scores(monkeypatch):
    seen = {}

    async def fake_call_json(**kw):
        seen.update(kw)
        return {
            "scores": {"positivity": {"value": 80, "evidence": [1, 2]}},
            "style": {"speech_level": "반말", "frequent_phrases": ["오 대박"]},
        }

    monkeypatch.setattr(agents_mod, "_call_json", fake_call_json)
    previous = ConversationStyle(speech_level="존댓말", frequent_phrases=["아하"])

    out = asyncio.run(ConversationAgent().analyze("상대: 뭐해?\n나[1]: 오 대박 진짜?", previous=previous))

    body = seen["messages"][0]["content"]
    assert "아하" in body and "나[1]: 오 대박 진짜?" in body and "상대: 뭐해?" in body
    assert out.style.speech_level == "반말"
    assert out.scores["positivity"].value == 80 and out.scores["positivity"].evidence == [1, 2]


def test_agent_wraps_llm_error(monkeypatch):
    async def boom(**kw):
        raise agents_mod.LLMError("timeout")

    monkeypatch.setattr(agents_mod, "_call_json", boom)

    with pytest.raises(ExtractionFailed):
        asyncio.run(ConversationAgent().analyze("나[1]: 안녕", previous=None))


def test_agent_wraps_non_object_style(monkeypatch):
    async def weird(**kw):
        return {"style": "반말"}

    monkeypatch.setattr(agents_mod, "_call_json", weird)

    with pytest.raises(ExtractionFailed):
        asyncio.run(ConversationAgent().analyze("나[1]: 안녕", previous=None))


def test_scores_drop_unknown_dimensions_bad_values_and_bad_shapes():
    out = V2Extraction.model_validate(
        {
            "scores": {
                "disclosure": {"value": 70, "evidence": [1, "2", True, 3]},
                "made_up": {"value": 10, "evidence": [1]},
                "openness": {"value": 101, "evidence": [1]},
                "positivity": {"value": "높음", "evidence": [1]},
                "avoidance": "높음",
                "anxiety": {"value": 30},
            }
        }
    )

    assert set(out.scores) == {"disclosure", "anxiety"}
    assert out.scores["disclosure"].evidence == [1, 3]  # 숫자가 아닌 근거는 버린다
    assert out.scores["anxiety"].evidence == []


def test_scores_that_are_not_an_object_become_empty():
    assert V2Extraction.model_validate({"scores": ["disclosure"]}).scores == {}
