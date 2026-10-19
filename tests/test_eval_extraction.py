"""추출 평가 채점 — F3 게이트 지표가 데이터셋 계약대로 계산되는지 고정한다. 모델 호출은 가짜로 바꾼다."""

import json

import pytest

from app.features.persona.agents import BuildFailed, ExtractionAgent
from app.features.persona.schemas import SCORED, RawExtraction
from scripts import eval_extraction as ev

EXPECTED = {
    "autoMetrics": {
        "applies": True,
        "scoreInRange": {"anxiety": [70, 95]},
        "mustOmitFromRawModel": ["avoidance", "openness"],
    }
}


def test_only_label_accuracy_cases_are_scored():
    cases = ev.load_cases(["regression"])

    assert len(cases) == 18  # 03_F3 4.1절 regression 유효 모수
    assert {c["metadata"]["evaluationKind"] for c in cases} == {"label_accuracy"}


def test_score_counts_judgment_range_and_omission():
    # anxiety 범위 안 ✅, avoidance 생략 ✅, openness 지어냄 ❌
    result = ev.score({"anxiety": 80, "openness": 50}, EXPECTED)

    assert result == {
        "failed": False,
        "judgments": [2, 3],
        "in_range": [1, 1],
        "unknown_omitted": [1, 2],
    }


def test_failure_counts_every_dimension_wrong():
    result = ev.score(None, EXPECTED)

    assert result["failed"] is True
    assert result["judgments"] == [0, 3] and result["unknown_omitted"] == [0, 2]


def test_gate_needs_full_omission_range_and_low_failure():
    good = {"case_id": "a", "latency_s": 1.0, "score": ev.score({"anxiety": 80}, EXPECTED)}
    leaky = {"case_id": "b", "latency_s": 2.0, "score": ev.score({"anxiety": 80, "avoidance": 50}, EXPECTED)}

    assert ev.summarize([good])["gate"]["passed"] is True
    summary = ev.summarize([good, leaky])
    assert summary["unknown_omitted_pct"] == 75.0
    assert summary["gate"]["passed"] is False


def test_holdout_needs_explicit_flag():
    with pytest.raises(SystemExit):
        ev._arguments(["--split", "blind_holdout"])


def test_main_runs_serving_agent_and_writes_report(tmp_path, monkeypatch):
    calls = []

    async def fake_extract(self, history, *, answered=None, **_):
        calls.append(answered)
        if len(calls) == 1:
            raise BuildFailed("timeout")
        return RawExtraction.model_validate({key: 60 for key in answered if key in SCORED})

    monkeypatch.setattr(ExtractionAgent, "extract", fake_extract)
    out = tmp_path / "report.json"

    assert ev.main(["--out", str(out), "--concurrency", "1"]) == 0

    report = json.loads(out.read_text())
    assert report["summary"]["cases"] == 18
    assert report["summary"]["failure_pct"] == round(100 / 18, 1)
    assert all(isinstance(a, set) for a in calls)  # answeredDimensions 를 운영처럼 집합으로 넘긴다
