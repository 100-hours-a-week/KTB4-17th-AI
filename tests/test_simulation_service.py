import asyncio

import pytest
from conftest import seed_persona, with_db

from app.features.persona.schemas import PersonaRef, PersonaResponse
from app.features.simulation.agents import LLMError, ReportAgent, SimulationAgent, SimulationFailed
from app.features.simulation.report import build_report, dimension_score, overall_score
from app.features.simulation.schemas import (
    Fit,
    ReportInput,
    ReportNarrative,
    ScriptLine,
    ScriptOutput,
    SimulationRequest,
    grade_of,
)
from app.features.simulation.service import PersonaNotFound, SimulationNotFound, SimulationService, normalize_script

# ── 대본 정리 규칙 (LLM 이 순서·줄 수를 틀릴 때) ─────────


def _lines(*pairs):
    return [ScriptLine(speaker=s, text=t) for s, t in pairs]


def test_same_speaker_in_a_row_is_merged_into_one_line():
    turns = normalize_script(_lines(("a", "안녕하세요"), ("a", "반가워요"), ("b", "네 안녕하세요")), turns=3)

    assert [(t.index, t.speaker, t.text) for t in turns] == [(0, "a", "안녕하세요 반가워요"), (1, "b", "네 안녕하세요")]


def test_lines_before_first_a_are_dropped_and_blank_lines_skipped():
    turns = normalize_script(_lines(("b", "먼저 말함"), ("a", "   "), ("a", "안녕"), ("b", "응")), turns=3)

    assert [(t.speaker, t.text) for t in turns] == [("a", "안녕"), ("b", "응")]


def test_script_longer_than_requested_turns_is_cut():
    script = _lines(*[("a" if i % 2 == 0 else "b", f"줄{i}") for i in range(10)])

    turns = normalize_script(script, turns=3)

    assert [t.text for t in turns] == ["줄0", "줄1", "줄2", "줄3", "줄4", "줄5"]


# ── 점수 규칙 (LLM 없음, 같은 입력 → 같은 결과) ──────────


@pytest.mark.parametrize(
    ("fit", "a", "b", "expected"),
    [
        (Fit.SIMILAR, 30, 70, 60),
        (Fit.BOTH_HIGH, 80, 60, 70),
        (Fit.BOTH_LOW, 20, 40, 70),
        (Fit.JUDGED, 50, 50, None),
    ],
)
def test_dimension_score_by_fit_rule(fit, a, b, expected):
    assert dimension_score(fit, a, b) == expected


@pytest.mark.parametrize(("score", "grade"), [(70, "GOOD"), (69, "OK"), (45, "OK"), (44, "CAUTION")])
def test_grade_boundaries(score, grade):
    assert grade_of(score) == grade


def test_overall_weights_conflict_and_orientation_heavier_and_subtracts_risk_penalty():
    areas = {"intimacy": 40, "communication": 40, "conflict": 80, "ideal": None, "orientation": 80}
    risk = type("R", (), {"penalty": 8})()

    # (40·1 + 40·1 + 80·1.5 + 80·1.5) / 5 = 64
    assert overall_score(areas, []) == 64
    assert overall_score(areas, [risk]) == 56
    assert overall_score({"intimacy": None}, []) == 50


# ── 리포트 서술 폴백 ─────────────────────────────────


ANXIOUS = PersonaResponse(persona_id="pa", scores={"anxiety": 80})
AVOIDANT = PersonaResponse(persona_id="pb", scores={"avoidance": 80})


class BrokenReportAgent(ReportAgent):
    async def write(self, **kwargs):
        raise LLMError("timeout")


def test_report_falls_back_to_template_when_llm_fails():
    report = asyncio.run(build_report(ReportInput(persona_a=ANXIOUS, persona_b=AVOIDANT), BrokenReportAgent()))

    assert report.narrative_source == "template"
    assert report.overall.headline != ""


class ForgetfulReportAgent(ReportAgent):
    async def write(self, **kwargs):
        return ReportNarrative(headline="잘 맞아요", summary="요약", cautions=["주말 계획을 미리 맞춰보세요"])


def test_risk_caution_is_always_in_report_even_if_llm_omits_it():
    report = asyncio.run(build_report(ReportInput(persona_a=AVOIDANT, persona_b=ANXIOUS), ForgetfulReportAgent()))

    assert report.narrative_source == "llm"
    assert report.risks == ["pursue_withdraw"]
    assert report.cautions[0] == "주말 계획을 미리 맞춰보세요"
    assert any("연락 기대치를 초반에 맞추는" in c for c in report.cautions)


# ── run: 페르소나 두 개 → 대본 + 리포트 저장 ────────────


class FakeSimulationAgent(SimulationAgent):
    def __init__(self, script=None, error=None):
        self.script = script
        self.error = error

    async def run(self, **kwargs):
        if self.error:
            raise self.error
        return self.script


def _script(lines, highlight_turns=()):
    return ScriptOutput.model_validate(
        {
            "transcript": [{"speaker": s, "text": t} for s, t in lines],
            "report": {
                "headline": "잘 맞는 두 사람",
                "summary": "대화가 잘 이어져요.",
                "highlights": [
                    {"kind": "click", "turn_index": i, "quote": "인용", "why": "이유"} for i in highlight_turns
                ],
            },
        }
    )


def _run(agent, **req):
    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="me", user_id="u-me", nickname="민수")
            await seed_persona(db, persona_id="partner", user_id="u-partner", nickname="지수")
        async with factory() as db:
            service = SimulationService(db)
            service.agent = agent
            try:
                result = await service.run(SimulationRequest(me_user_id="u-me", turns=3, **req))
                await db.commit()
            except Exception as e:
                await db.rollback()
                result = e
        async with factory() as db:
            listed = await SimulationService(db).list_for(PersonaRef(user_id="u-me"))
            fetched = await SimulationService(db).get(result.simulation_id) if listed else None
        return result, listed, fetched

    return asyncio.run(with_db(scenario))


def test_run_saves_normalized_transcript_and_report():
    lines = [("a", "안녕하세요"), ("b", "반가워요"), ("a", "주말에 뭐 해요?"), ("b", "러닝해요")]
    result, listed, fetched = _run(FakeSimulationAgent(_script(lines)), partner_user_id="u-partner")

    assert [(t.speaker, t.text) for t in result.transcript] == lines
    assert (result.me.nickname, result.partner.nickname) == ("민수", "지수")
    assert result.report.narrative_source == "llm"
    assert result.report.simulation_id == result.simulation_id
    assert [s.simulation_id for s in listed] == [result.simulation_id]
    assert fetched.transcript == result.transcript


def test_run_drops_highlights_pointing_past_the_transcript():
    lines = [("a", "안녕"), ("b", "네"), ("a", "뭐 해요"), ("b", "쉬어요")]
    result, _, _ = _run(FakeSimulationAgent(_script(lines, highlight_turns=(1, 3, 4, 99))), partner_user_id="u-partner")

    assert [h.turn_index for h in result.report.highlights] == [1, 3]


def test_run_fails_and_saves_nothing_when_script_has_no_a_line():
    lines = [("b", "안녕"), ("b", "누구세요")]
    result, listed, _ = _run(FakeSimulationAgent(_script(lines)), partner_user_id="u-partner")

    assert isinstance(result, SimulationFailed)
    assert listed == []


def test_run_with_unknown_partner_is_persona_not_found():
    result, listed, _ = _run(
        FakeSimulationAgent(error=AssertionError("LLM 을 부르면 안 됨")), partner_user_id="u-nobody"
    )

    assert isinstance(result, PersonaNotFound)
    assert result.who == "partner"
    assert listed == []


def test_get_unknown_simulation_is_not_found():
    async def scenario(factory):
        async with factory() as db:
            await SimulationService(db).get("nope")

    with pytest.raises(SimulationNotFound):
        asyncio.run(with_db(scenario))
