import asyncio
import json

import pytest

from app.features.persona.schemas import PersonaResponse
from app.features.simulation import agents
from app.features.simulation.agents import LLMError, ReportAgent, SimulationAgent, SimulationFailed
from app.features.simulation.schemas import Transcript


def _llm_returns(monkeypatch, text):
    """_call(LLM 호출 경계)만 가짜로 — 그 뒤의 JSON 추출·스키마 검증은 진짜 코드가 돈다."""
    seen = {}

    async def fake_call(*, system, messages, max_tokens, timeout):
        seen.update(system=system, messages=messages, max_tokens=max_tokens)
        if isinstance(text, Exception):
            raise text
        return text

    monkeypatch.setattr(agents, "_call", fake_call)
    return seen


def _parse(monkeypatch, text):
    _llm_returns(monkeypatch, text)
    return asyncio.run(agents._call_json(system="", messages=[], max_tokens=1, timeout=1))


@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```JSON\n{"a": 1}\n```',
        '결과입니다:\n```json\n{"a": 1}\n```',
        '다음과 같아요.\n{"a": 1}\n이상입니다.',
    ],
)
def test_json_is_extracted_from_common_llm_wrappings(monkeypatch, raw):
    assert _parse(monkeypatch, raw) == {"a": 1}


def test_empty_or_non_json_reply_is_llm_error(monkeypatch):
    with pytest.raises(LLMError):
        _parse(monkeypatch, "죄송해요, 지금은 답할 수 없어요.")


PERSONA = PersonaResponse(persona_id="p", scores={})
NARRATIVE = {"headline": "연락 리듬이 맞는 두 사람", "summary": "잘 맞아요."}


def _run_simulation(turns=3):
    return asyncio.run(
        SimulationAgent().run(
            persona_a=PERSONA,
            persona_b=PERSONA,
            name_a="민수",
            name_b="지수",
            turns=turns,
            area_scores={},
            dim_scores={},
        )
    )


def test_simulation_parses_script_and_report(monkeypatch):
    payload = {
        "transcript": [{"speaker": "a", "text": "안녕하세요"}, {"speaker": "b", "text": "반가워요"}],
        "report": {**NARRATIVE, "unknown_key": "무시"},
        "extra": "무시",
    }
    _llm_returns(monkeypatch, "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```")

    out = _run_simulation()

    assert [(line.speaker, line.text) for line in out.transcript] == [("a", "안녕하세요"), ("b", "반가워요")]
    assert out.report.headline == "연락 리듬이 맞는 두 사람"


def test_simulation_asks_for_requested_turns_with_token_budget(monkeypatch):
    seen = _llm_returns(monkeypatch, LLMError("stop here"))

    with pytest.raises(SimulationFailed):
        _run_simulation(turns=5)

    user = seen["messages"][0]["content"]
    assert "턴 수: 5 왕복" in user
    assert "총 10줄" in user
    assert seen["max_tokens"] == 2200 + 180 * 5


@pytest.mark.parametrize(
    "reply",
    [
        LLMError("timeout after 120s"),
        "대본을 못 썼어요",
        json.dumps({"transcript": [{"speaker": "a", "text": "안녕"}], "report": NARRATIVE}),
        json.dumps(
            {"transcript": [{"speaker": "a", "text": "안녕"}, {"speaker": "c", "text": "?"}], "report": NARRATIVE}
        ),
        json.dumps({"transcript": [{"speaker": "a", "text": "a"}, {"speaker": "b", "text": "b"}]}),
    ],
    ids=["llm-error", "not-json", "one-line", "unknown-speaker", "no-report"],
)
def test_simulation_bad_llm_output_is_simulation_failed(monkeypatch, reply):
    _llm_returns(monkeypatch, reply)

    with pytest.raises(SimulationFailed):
        _run_simulation()


def _write_report(transcript=None):
    return asyncio.run(
        ReportAgent().write(
            persona_a=PERSONA,
            persona_b=PERSONA,
            transcript=transcript or Transcript(),
            name_a="민수",
            name_b="지수",
            area_scores={},
            dim_scores={},
        )
    )


def test_report_agent_parses_narrative(monkeypatch):
    _llm_returns(monkeypatch, json.dumps({**NARRATIVE, "ideal_fit": {"ideal_warmth": 80}}, ensure_ascii=False))

    narrative = _write_report()

    assert narrative.headline == "연락 리듬이 맞는 두 사람"
    assert narrative.ideal_fit == {"ideal_warmth": 80}


def test_report_agent_says_when_there_is_no_transcript(monkeypatch):
    seen = _llm_returns(monkeypatch, json.dumps(NARRATIVE, ensure_ascii=False))

    _write_report()

    assert "(대화록 없음 — 페르소나만으로 서술)" in seen["messages"][0]["content"]


def test_report_agent_invalid_narrative_is_llm_error(monkeypatch):
    _llm_returns(monkeypatch, json.dumps({"headline": "가" * 61, "summary": "요약"}))

    with pytest.raises(LLMError):
        _write_report()
