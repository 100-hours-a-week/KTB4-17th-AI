"""모델 원문이 운영 파서의 보정으로 정답이 되는 평가 회귀를 막는다."""

import asyncio
import json
from argparse import Namespace
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.features.persona import agents
from scripts import eval_tagging, run_langfuse_calibration


def empty_case():
    return next(
        case
        for case in eval_tagging.load_cases(["calibration", "regression"])
        if case["metadata"]["caseId"] == "persona_onboarding_tagging-007"
    )


def run_case(monkeypatch, raw, runner, case=None):
    case = case or empty_case()

    async def model(**kwargs):
        return json.dumps(raw)

    monkeypatch.setattr(agents, "_call", model)
    if runner == "standalone":
        monkeypatch.setattr(eval_tagging, "load_cases", lambda *args: [case])
        args = Namespace(timeout=15, concurrency=1, split=["regression"], dataset=None)
        return asyncio.run(eval_tagging._run(args))["results"][0]
    item = SimpleNamespace(
        id=case["metadata"]["caseId"],
        dataset_name="quality/persona/onboarding-tagging",
        metadata=case["metadata"],
        input=case["input"],
        expected_output=case["expectedOutput"],
    )
    return asyncio.run(run_langfuse_calibration.run_item(item=item))


INVALID_OUTPUTS = [
    {"primary": ["invented_dimension"], "secondary": [], "off_topic": False},
    {"primary": [], "secondary": ["invented_dimension"], "off_topic": False},
    {},
    {"primary": [], "secondary": []},
    {"primary": [], "secondary": [], "off_topic": 0},
    {"primary": [], "secondary": [], "off_topic": "false"},
    {"primary": [], "secondary": [], "off_topic": False, "score": 90},
    {"primary": [], "secondary": "", "off_topic": False},
    {"primary": [], "secondary": None, "off_topic": False},
    {"primary": [123], "secondary": [], "off_topic": False},
    [],
    None,
]


@pytest.mark.parametrize("runner", ["standalone", "langfuse"])
@pytest.mark.parametrize("raw", INVALID_OUTPUTS)
def test_invalid_raw_output_cannot_become_a_correct_empty_answer(monkeypatch, runner, raw):
    result = run_case(monkeypatch, raw, runner)
    if runner == "standalone":
        assert result["score"]["exact"] is False
        assert result["score"]["failed"] is True
    else:
        assert result["automaticScore"] == 0.0


@pytest.mark.parametrize("raw", INVALID_OUTPUTS)
def test_standalone_scorer_rejects_malformed_payloads_without_crashing(raw):
    result = eval_tagging.score(raw, empty_case()["expectedOutput"])
    assert result == {"primary": False, "secondary": False, "off_topic": False, "exact": False, "failed": True}


@pytest.mark.parametrize("runner", ["standalone", "langfuse"])
def test_valid_empty_answer_still_passes(monkeypatch, runner):
    result = run_case(monkeypatch, {"primary": [], "secondary": [], "off_topic": False}, runner)
    if runner == "standalone":
        assert result["score"]["exact"] is True
        assert result["score"]["failed"] is False
    else:
        assert result["automaticScore"] == 1.0


@pytest.mark.parametrize("runner", ["standalone", "langfuse"])
def test_both_runners_use_set_matching(monkeypatch, runner):
    case = deepcopy(empty_case())
    case["expectedOutput"]["autoMetrics"]["primary"]["accepted"] = [["avoidance", "routine"]]
    raw = {"primary": ["routine", "avoidance"], "secondary": [], "off_topic": False}
    result = run_case(monkeypatch, raw, runner, case)
    if runner == "standalone":
        assert result["score"]["exact"] is True
    else:
        assert result["automaticScore"] == 1.0


def test_production_parser_keeps_existing_lenient_behavior():
    tags = agents.parse_tags({"primary": ["invented_dimension"]})
    assert tags.model_dump() == {"primary": [], "secondary": [], "off_topic": False}


@pytest.mark.parametrize("runner", ["standalone", "langfuse"])
def test_valid_but_wrong_label_does_not_pass(monkeypatch, runner):
    result = run_case(monkeypatch, {"primary": ["routine"], "secondary": [], "off_topic": False}, runner)
    if runner == "standalone":
        assert result["score"]["primary"] is False
        assert result["score"]["exact"] is False
        assert result["score"]["failed"] is False
    else:
        assert result["automaticChecks"]["primary"] is False
        assert result["automaticScore"] < 1.0


def test_fault_fixture_cannot_receive_a_model_label_pass(monkeypatch):
    case = deepcopy(empty_case())
    case["expectedOutput"]["autoMetrics"] = {"applies": False}

    async def unexpected_call(**kwargs):
        pytest.fail("fault fixtures must not call the real model")

    monkeypatch.setattr(agents.TaggingAgent, "tag", unexpected_call)
    result = run_case(monkeypatch, {}, "langfuse", case)
    assert result["status"] == "error"
    assert result["automaticScore"] == 0.0
