"""품질 데이터셋 기대값이 현재 앱 코드와 어긋나지 않았는지 검사한다.

정답은 앱 코드다. 생성기(scripts/generate_quality_datasets.py)를 거치지 않고
앱의 순수 함수를 직접 호출해 JSONL의 기대값과 비교한다. 네트워크·모델 호출은 없다.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from app.features.persona.schemas import CONFIDENCE_LOW, SCORED, PersonaResponse
from app.features.simulation.report import overall_score, score_layer

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "evals" / "quality_datasets"
AGENTS_SRC = (ROOT / "app" / "features" / "simulation" / "agents.py").read_text("utf-8")

# 앱이 백엔드 계약(#63) 때문에 모름 차원의 표시값(a/b)으로 내보내는 값
BACKEND_UNKNOWN_DISPLAY = 50


def rows(name: str) -> list[dict]:
    return [json.loads(line) for line in (DATA / f"{name}.jsonl").read_text("utf-8").splitlines() if line]


def cid(row: dict) -> str:
    return row["metadata"]["caseId"]


def as_persona(raw: dict) -> PersonaResponse:
    return PersonaResponse.model_validate(raw)


def app_max_tokens(turns: int) -> int:
    """simulation/agents.py 의 max_tokens 공식을 소스에서 읽는다 (하드코딩 복제 방지)."""
    match = re.search(r"max_tokens\s*=\s*(\d+)\s*\+\s*(\d+)\s*\*\s*turns", AGENTS_SRC)
    assert match, "simulation/agents.py 에서 max_tokens 공식을 찾지 못했다 — 테스트를 갱신해야 한다"
    return int(match.group(1)) + int(match.group(2)) * turns


def test_dataset_shape_unchanged():
    expected = {
        "persona_onboarding_conversation": 60,
        "persona_onboarding_tagging": 80,
        "persona_build": 60,
        "practice_reply": 70,
        "simulation_run": 36,
    }
    assert {n: len(rows(n)) for n in expected} == expected
    splits = Counter(r["metadata"]["split"] for n in expected for r in rows(n))
    assert (splits["calibration"], splits["regression"], splits["blind_holdout"]) == (61, 184, 61)


@pytest.mark.parametrize("row", rows("simulation_run"), ids=cid)
def test_simulation_run_runtime_matches_app(row):
    runtime = row["expectedOutput"]["runtime"]
    turns = row["input"]["turns"]
    assert runtime["maxTokens"] == app_max_tokens(turns)
    # 깨진·잘린 출력은 1회 재시도하므로 호출은 최대 2회다
    assert runtime["maxLlmCalls"] == 2


@pytest.mark.parametrize("row", rows("simulation_run"), ids=cid)
def test_simulation_fault_retry_contract(row):
    fault = (row["input"].get("faultInjection") or {}).get("kind")
    calls = row["expectedOutput"]["runtime"]["llmCalls"]
    if fault == "invalid_json":
        # 계속 깨지는 JSON: 재시도해도 실패해 SimulationFailed(invalid_json)로 끝난다
        assert calls == 2
        assert row["expectedOutput"]["faultContract"]["reason"] == "invalid_json"
    elif fault == "timeout":
        # timeout 은 재시도하지 않는다
        assert calls == 1


@pytest.mark.parametrize("row", rows("simulation_run"), ids=cid)
def test_unknown_persona_scores_are_null(row):
    """신뢰도 LOW 이고 근거 없는 차원은 50 이 아니라 null 이다."""
    for key in ("persona_a", "persona_b"):
        p = row["input"][key]
        assert set(p["scores"]) == set(SCORED)
        stale = [d for d, v in p["scores"].items() if v == 50 and p["confidence"][d] == CONFIDENCE_LOW]
        assert not stale, f"{key}: 근거 없는 50 이 남아 있다 {stale}"


@pytest.mark.parametrize("row", rows("simulation_run"), ids=cid)
def test_rule_oracle_matches_app(row):
    inp, oracle = row["input"], row["expectedOutput"]["ruleOracle"]
    pa, pb = as_persona(inp["persona_a"]), as_persona(inp["persona_b"])
    dims, areas, risks = score_layer(pa, pb, oracle.get("idealFitInput") or None)
    assert {d.dimension: d.score for d in dims} == oracle["dimensionScores"]
    assert areas == oracle["areaScores"]
    assert [r.id for r in risks] == oracle["riskIds"]
    assert overall_score(areas, risks) == oracle["overallScore"]
    # 모름 차원의 표시값은 백엔드 계약상 50, 궁합 score 는 null
    for d in dims:
        a, b = inp["persona_a"]["scores"][d.dimension], inp["persona_b"]["scores"][d.dimension]
        assert d.a == (BACKEND_UNKNOWN_DISPLAY if a is None else a)
        assert d.b == (BACKEND_UNKNOWN_DISPLAY if b is None else b)
        if (a is None or b is None) and d.fit.name != "JUDGED":
            assert d.score is None


@pytest.mark.parametrize("row", rows("persona_build"), ids=cid)
def test_build_unanswered_dimensions_are_null(row):
    post = row["expectedOutput"]["postService"]
    assert "defaultScores" not in post
    # all_confidence 는 15개 차원 값이 모두 주어지는 confidence_of·accuracy_of 후처리 검사라 모름 차원이 없다
    omitted = (
        set()
        if row["metadata"]["category"] == "all_confidence"
        else set(row["expectedOutput"]["rawModel"]["omitDimensions"])
    )
    assert set(post["unknownScores"]) == omitted
    assert all(v is None for v in post["unknownScores"].values())
    assert all(post["confidence"][d] == CONFIDENCE_LOW for d in omitted)


@pytest.mark.parametrize("row", rows("persona_build"), ids=cid)
def test_build_textual_fields_follow_answered_filter(row):
    """서비스는 텍스트 항목(관심사·일과·데이트 선호·기피)도 answered 밖이면 []로 버린다 (persona/service.py)."""
    textual = ("interests", "routine", "date_prefer", "date_avoid")
    answered = set(row["input"]["answeredDimensions"])
    assert answered == {k for k, v in row["input"]["coverage"]["primary"].items() if v}
    post = row["expectedOutput"]["postService"]
    assert set(post["textualKept"]) == {k for k in textual if k in answered}
    assert set(post["textualKept"]) | set(post["textualDropped"]) == set(textual)
    if row["metadata"]["category"].startswith("dimension_") or row["metadata"]["category"] == "textual_grounding":
        labels = row["expectedOutput"]["referenceLabels"]
        wanted = {k for k in textual if labels.get(k)}
        assert wanted <= set(post["textualKept"]), "정답 텍스트 라벨이 서비스 후처리에서 버려진다"


def _all_personas():
    for row in rows("simulation_run"):
        for key in ("persona_a", "persona_b"):
            yield pytest.param(row["input"][key], id=f"{cid(row)}-{key}")
    for row in rows("practice_reply"):
        yield pytest.param(row["input"]["partner"], id=f"{cid(row)}-partner")


@pytest.mark.parametrize("persona", list(_all_personas()))
def test_null_score_is_always_low_confidence(persona):
    """앱에서 값이 없는(null) 차원은 항상 LOW 다. null 인데 HIGH/MEDIUM 인 페르소나는 만들어질 수 없다."""
    bad = [d for d, v in persona["scores"].items() if v is None and persona["confidence"][d] != CONFIDENCE_LOW]
    assert not bad


@pytest.mark.parametrize("row", rows("simulation_run"), ids=cid)
def test_risk_penalty_dropped_is_flagged(row):
    oracle = row["expectedOutput"]["ruleOracle"]
    flags = row["metadata"]["reviewFlags"]
    dropped = bool(oracle["riskPenalty"]) and all(v is None for v in oracle["areaScores"].values())
    assert dropped == ("known_code_gap_risk_penalty_dropped_when_all_areas_null" in flags)


def test_risk_boundary_cases_show_penalty_in_total():
    """위험 64/65 경계가 총점에서도 구분되어야 한다 (양쪽이 아는 차원이 있는 경우)."""
    by_kind = {}
    for row in rows("simulation_run"):
        if row["metadata"]["category"] in {"pursue_64", "pursue_65"}:
            by_kind.setdefault(row["metadata"]["category"], set()).add(
                row["expectedOutput"]["ruleOracle"]["overallScore"]
            )
    assert by_kind["pursue_64"].isdisjoint(by_kind["pursue_65"])


def _out_of_scope_rows():
    return [r for r in rows("practice_reply") if r["metadata"]["category"] == "out_of_scope_request"]


def test_out_of_scope_cases_are_appended_with_stable_ids():
    """기존 60건의 caseId 를 밀지 않도록 61~70 으로 뒤에 붙는다. 분할은 프로필과 같은 2/6/2 다."""
    oos = _out_of_scope_rows()
    assert [cid(r) for r in oos] == [f"practice_reply-{n:03d}" for n in range(61, 71)]
    assert Counter(r["metadata"]["split"] for r in oos) == {"calibration": 2, "regression": 6, "blind_holdout": 2}


@pytest.mark.parametrize("row", _out_of_scope_rows(), ids=cid)
def test_out_of_scope_contract_and_prompt_build(row):
    contract = row["expectedOutput"]["outOfScopeContract"]
    history = row["input"]["history"]
    requests = [m["content"] for m in history if m["role"] == "user" and m["content"] == contract["request"]]
    assert history[-1]["content"] == contract["request"]
    assert len(requests) == 1 + contract["priorRequestCount"]
    assert "quality_contract_not_enforced_by_code" in row["metadata"]["reviewFlags"]
    # 앱의 실제 프롬프트 생성이 이 입력(null 점수 포함)에서 예외 없이 되는지
    from app.features.practice.agents import PartnerAgent

    prompt = PartnerAgent.system_prompt(
        partner_name=row["input"]["partnerName"],
        partner=as_persona(row["input"]["partner"]),
        my_name=row["input"]["myName"],
        me=None,
    )
    assert "None" not in prompt


IMMERSION_SKIP = (
    "faultInjection",
    "serviceProbe",
    "requestProbe",
    "ruleProbe",
    "candidateOutput",
    "candidateNarrative",
    "storedHistory",
)


def _immersion_rows():
    for name in ("persona_onboarding_conversation", "practice_reply", "simulation_run"):
        for row in rows(name):
            yield pytest.param(row, id=cid(row))


@pytest.mark.parametrize("row", list(_immersion_rows()))
def test_immersion_contract_applies_to_generation_cases(row):
    """자연어 발화를 만드는 케이스에는 몰입 기준이, 코드 경계·장애 케이스에는 없다."""
    ex = row["expectedOutput"]
    generates = not any(k in row["input"] for k in IMMERSION_SKIP) and row["metadata"]["category"] not in {
        "code_boundary",
        "stream_recovery",
    }
    assert ("immersionContract" in ex) == generates
    if not generates:
        return
    contract = ex["immersionContract"]
    assert contract["speakingAs"] == "소개팅 중인 20~30대"
    text = " ".join(ex["hardAssertions"])
    assert "20~30대" in text
    if row["metadata"]["category"] in {"identity_injection", "adversarial"}:
        # 정체를 진지하게 묻는 케이스는 기존 계약(AI라고 답하고 역할 유지)을 바꾸지 않는다
        assert contract["noSelfAIReference"] is False
        assert "스스로 AI" not in text
    else:
        assert contract["noSelfAIReference"] is True
        assert any("스스로 AI" in f for f in ex["forbidden"])


def test_identity_contract_is_not_weakened():
    adversarial = [r for r in rows("practice_reply") if r["metadata"]["category"] == "adversarial"]
    calibration = [r for r in adversarial if r["metadata"]["split"] == "calibration"]
    assert calibration
    for r in calibration:
        contract = r["expectedOutput"]["adversarialContract"]
        assert contract["identityWhenAsked"] == "가상다온님의 페르소나를 연기하는 AI"
        assert contract["retainPersona"] is True


@pytest.mark.parametrize("row", rows("persona_onboarding_conversation"), ids=cid)
def test_conversation_scoring_by_evaluation_kind(row):
    ex, kind = row["expectedOutput"], row["metadata"]["evaluationKind"]
    criteria = ex["rubric"]["criteria"]
    assert sum(criteria.values()) == 100
    if kind == "language_quality":
        assert criteria == {
            "topicIntent": 20,
            "turnSpecificContract": 20,
            "previousAnswerResponse": 20,
            "conversationalTone": 20,
            "naturalQuestion": 20,
        }
        assert set(ex["rubric"]["criteriaGuide"]) == set(criteria)
        # 몰입 실패는 총점 상한 없이 해당 항목만 감점한다
        assert all(
            "상한은 두지 않는다" in d["effect"] or "이 항목만" in d["effect"] for d in ex["rubric"]["deductions"]
        )
        assert "immersionContract" in ex
    else:
        assert kind == "code_behavior"
        assert "naturalQuestion" not in criteria
        assert "immersionContract" not in ex


def test_conversation_kind_counts():
    kinds = Counter(r["metadata"]["evaluationKind"] for r in rows("persona_onboarding_conversation"))
    assert kinds == {"language_quality": 28, "code_behavior": 32}


@pytest.mark.parametrize("row", rows("persona_onboarding_tagging"), ids=cid)
def test_tagging_auto_metrics(row):
    ex, meta = row["expectedOutput"], row["metadata"]
    labels = ex["referenceLabels"]
    if "faultInjection" in row["input"]:
        assert meta["evaluationKind"] == "code_behavior"
        assert ex["autoMetrics"]["applies"] is False
        return
    assert meta["evaluationKind"] == "label_accuracy"
    metrics = ex["autoMetrics"]
    assert metrics["applies"] is True
    assert labels["primary"] in metrics["primary"]["accepted"]
    assert labels["secondary"] in metrics["secondary"]["accepted"]
    assert labels["off_topic"] in metrics["off_topic"]["accepted"]
    assert ex["rubric"]["role"] == "auxiliary"
    universe = set(ex["labelUniverse"])
    assert set(labels["primary"]) <= universe and set(labels["secondary"]) <= universe


@pytest.mark.parametrize("row", rows("persona_build"), ids=cid)
def test_build_scoring_by_evaluation_kind(row):
    ex, meta = row["expectedOutput"], row["metadata"]
    dimension_case = meta["category"].startswith("dimension_")
    assert meta["evaluationKind"] == ("label_accuracy" if dimension_case else "code_behavior")
    metrics = ex["autoMetrics"]
    assert metrics["applies"] is True
    if dimension_case:
        assert metrics["scoreInRange"] == {d: [r["min"], r["max"]] for d, r in ex["acceptableRanges"].items()}
        assert metrics["unknownMustBeNull"] == sorted(ex["postService"]["unknownScores"])
        assert ex["rubric"]["role"] == "auxiliary"
    else:
        assert metrics["kind"] == "service_postprocessing"


def test_build_kind_counts():
    kinds = Counter(r["metadata"]["evaluationKind"] for r in rows("persona_build"))
    assert kinds == {"label_accuracy": 30, "code_behavior": 30}


@pytest.mark.parametrize("row", rows("practice_reply"), ids=cid)
def test_practice_scoring_by_evaluation_kind(row):
    ex, meta = row["expectedOutput"], row["metadata"]
    criteria = ex["rubric"]["criteria"]
    assert sum(criteria.values()) == 100
    if "immersionContract" not in ex:
        assert meta["evaluationKind"] == "code_behavior"
        assert ex["autoMetrics"]["kind"] == "service_or_stream_behavior"
        return
    assert meta["evaluationKind"] == "language_quality"
    checks = ex["autoMetrics"]["formatChecks"]
    assert checks["questionCount"]["max"] == 1
    assert checks["sentenceCount"]["min"] >= 1
    if meta["category"] == "out_of_scope_request":
        assert criteria["requestHandling"] == 35
    else:
        assert criteria == {"personaFacts": 30, "messageContinuity": 25, "caseContract": 20, "tone": 25}
    # 몰입 실패는 이 기능에서는 1점 문구에 포함한다 (온보딩 발화와 다른 방식)
    if meta["category"] not in {"identity_injection", "adversarial"}:
        assert "스스로 AI" in ex["rubric"]["anchors"]["1"]


def test_practice_kind_counts():
    kinds = Counter(r["metadata"]["evaluationKind"] for r in rows("practice_reply"))
    assert kinds == {"language_quality": 48, "code_behavior": 22}


@pytest.mark.parametrize("row", rows("simulation_run"), ids=cid)
def test_simulation_scoring_by_evaluation_kind(row):
    ex, meta = row["expectedOutput"], row["metadata"]
    criteria = ex["rubric"]["criteria"]
    assert sum(criteria.values()) == 100
    if "immersionContract" not in ex:
        assert meta["evaluationKind"] == "code_behavior"
        assert ex["autoMetrics"]["kind"] == "script_normalization_or_fault"
        return
    assert meta["evaluationKind"] == "language_quality"
    assert criteria["conversationalTone"] == 20
    checks = ex["autoMetrics"]["formatChecks"]
    assert checks["lineCount"] == 2 * row["input"]["turns"]
    assert checks["highlightIndexRange"] == ex["highlightContract"]["indexRange"]
    assert any("자기 닉네임" in a for a in ex["hardAssertions"])
    assert any("자기 프로필에 있는 것만" in a for a in ex["hardAssertions"])


def test_simulation_kind_counts():
    kinds = Counter(r["metadata"]["evaluationKind"] for r in rows("simulation_run"))
    assert kinds == {"language_quality": 18, "code_behavior": 18}


def test_every_dataset_has_evaluation_kind():
    for name in (
        "persona_onboarding_conversation",
        "persona_onboarding_tagging",
        "persona_build",
        "practice_reply",
        "simulation_run",
    ):
        assert all("evaluationKind" in r["metadata"] for r in rows(name)), name
