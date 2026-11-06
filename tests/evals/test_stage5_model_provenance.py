"""SLM 평가 결과에 실제로 답한 모델이 남는지. Gemini 폴백 점수가 학생 모델 점수로 집계되면 배포 게이트가 무의미하다."""

import json

import pytest

from evals._harness import stage6_scorecard as S
from evals._harness.common import slm_provenance

STUDENT = "extraction-v1"
GEMINI = "google/gemini-2.5-flash-lite"


def answered(model: str) -> dict:
    return {"model": model, "output": "{}"}


def failed(model: str) -> dict:
    return {"model": model, "error": "APIConnectionError"}


@pytest.mark.parametrize(
    ("records", "slm_model", "model", "fallback_used"),
    [
        pytest.param([answered(STUDENT)], STUDENT, STUDENT, False, id="slm-answered"),
        pytest.param([answered(STUDENT), answered(GEMINI)], STUDENT, GEMINI, True, id="slm-broke-json"),
        pytest.param([failed(STUDENT), answered(GEMINI)], STUDENT, GEMINI, True, id="slm-call-failed"),
        pytest.param([answered(GEMINI)], STUDENT, GEMINI, True, id="slm-marked-down-or-timed-out"),
        pytest.param([failed(STUDENT)], STUDENT, None, True, id="nobody-answered"),
        pytest.param([], STUDENT, None, True, id="no-call"),
        pytest.param([answered(GEMINI)], "", GEMINI, False, id="baseline-without-slm"),
    ],
)
def test_provenance_reports_the_model_that_actually_answered(records, slm_model, model, fallback_used):
    assert slm_provenance(records, slm_model) == {"model": model, "fallback_used": fallback_used}


def test_scorecard_exposes_fallback_cases(tmp_path):
    verdict = {t: "PASS" for t in S.get_all_defined_types("f03")}
    rows = [
        {
            "case_id": f"c{i}",
            "split": "regression",
            "sample": {"output_raw": "{}", **slm_provenance(records, STUDENT)},
            "verdict": verdict,
        }
        for i, records in enumerate(([answered(STUDENT)], [answered(STUDENT), answered(GEMINI)]))
    ]
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"feature": "f03", "split": "regression", "rows": rows}), encoding="utf-8")

    card = S.parse_and_score(result)["scorecard"]

    assert card.fallback_used_count == 1
    assert card.app_observed_model == f"{STUDENT}, {GEMINI}"
