"""태깅 F1·추출 차원 판정 정확도 지표 — 학습 모델 비교의 기준이므로 계산 규칙을 고정한다."""

import json
from pathlib import Path

import pytest

from scripts import quality_metrics as qm

DATASETS = Path("evals/quality_datasets")


def _tagging_expected(primary, secondary=([],), off_topic=(False,)):
    return {
        "autoMetrics": {
            "applies": True,
            "primary": {"accepted": [list(p) for p in primary]},
            "secondary": {"accepted": [list(s) for s in secondary]},
            "off_topic": {"accepted": list(off_topic)},
        }
    }


def _tagging_output(primary=(), secondary=(), off_topic=False, *, status="ok", mode="model"):
    result = {"primary": list(primary), "secondary": list(secondary), "off_topic": off_topic}
    return {"status": status, "mode": mode, "result": result}


TAG_INPUT = {"task": "persona_tagging"}
BUILD_INPUT = {"task": "persona_build"}


def _by_name(metrics):
    return {m.name: m.value for m in metrics}


# ── 태깅 ──────────────────────────────────────────────


def test_tagging_micro_f1_pools_counts_across_items():
    rows = [
        # tp=1
        (TAG_INPUT, _tagging_output(["anxiety"]), _tagging_expected([["anxiety"]])),
        # tp=1, fp=1
        (TAG_INPUT, _tagging_output(["anxiety", "avoidance"]), _tagging_expected([["anxiety"]])),
        # fn=1
        (TAG_INPUT, _tagging_output([]), _tagging_expected([["disclosure"]])),
    ]

    metrics = _by_name(qm.tagging_run_metrics(rows))

    assert metrics["tagging_primary_precision"] == pytest.approx(2 / 3)
    assert metrics["tagging_primary_recall"] == pytest.approx(2 / 3)
    assert metrics["tagging_primary_f1"] == pytest.approx(2 / 3)
    assert metrics["tagging_exact_match"] == pytest.approx(1 / 3)


def test_tagging_uses_the_closest_accepted_alternative():
    expected = _tagging_expected([["anxiety"], ["anxiety", "contact_rhythm"]])
    output = _tagging_output(["anxiety", "contact_rhythm"])

    metrics = _by_name(qm.item_metrics(TAG_INPUT, output, expected))

    assert metrics["tagging_primary_f1"] == 1.0
    assert metrics["tagging_exact_match"] == 1.0


def test_tagging_failure_counts_as_wrong_not_skipped():
    failed = {"status": "ok", "mode": "model", "result": None}
    rows = [
        (TAG_INPUT, _tagging_output(["anxiety"]), _tagging_expected([["anxiety"]])),
        (TAG_INPUT, failed, _tagging_expected([["avoidance"]])),
    ]

    metrics = _by_name(qm.tagging_run_metrics(rows))

    assert metrics["tagging_primary_recall"] == pytest.approx(1 / 2)
    assert metrics["tagging_exact_match"] == pytest.approx(1 / 2)
    assert metrics["tagging_failure_rate"] == pytest.approx(1 / 2)
    assert _by_name(qm.item_metrics(TAG_INPUT, failed, _tagging_expected([[]])))["tagging_primary_f1"] == 0.0


def test_tagging_off_topic_reports_positive_cases():
    rows = [
        (TAG_INPUT, _tagging_output(off_topic=True), _tagging_expected([[]], off_topic=(True,))),
        (TAG_INPUT, _tagging_output(off_topic=False), _tagging_expected([[]], off_topic=(True,))),
        (TAG_INPUT, _tagging_output(["anxiety"]), _tagging_expected([["anxiety"]])),
    ]

    off_topic = next(m for m in qm.tagging_run_metrics(rows) if m.name == "tagging_off_topic_accuracy")

    assert off_topic.value == pytest.approx(2 / 3)
    assert "1/2" in off_topic.comment


def test_fixture_and_not_applicable_items_are_excluded():
    expected = _tagging_expected([["anxiety"]])
    not_applicable = {"autoMetrics": {**expected["autoMetrics"], "applies": False}}
    rows = [
        (TAG_INPUT, _tagging_output(["anxiety"], mode="fixture"), expected),
        (TAG_INPUT, _tagging_output([]), not_applicable),
    ]

    assert qm.tagging_run_metrics(rows) == []


# ── 추출 ──────────────────────────────────────────────


def _build_expected(ranges, omitted):
    return {"autoMetrics": {"applies": True, "scoreInRange": ranges, "mustOmitFromRawModel": omitted}}


def _build_output(raw, *, status="ok"):
    return {"status": status, "mode": "model", "result": {"raw": raw}}


def test_build_judgment_needs_value_in_range_or_omitted():
    expected = _build_expected({"anxiety": [70, 95], "avoidance": [0, 30]}, ["openness", "seriousness"])
    # anxiety 범위 안 ✅, avoidance 범위 밖 ❌, openness 생략 ✅, seriousness 지어냄 ❌
    output = _build_output({"anxiety": 80, "avoidance": 50, "openness": None, "seriousness": 50})

    metrics = _by_name(qm.build_run_metrics([(BUILD_INPUT, output, expected)]))

    assert metrics["build_dimension_judgment_accuracy"] == pytest.approx(2 / 4)
    assert metrics["build_dimension_presence_accuracy"] == pytest.approx(3 / 4)
    assert metrics["build_score_in_range_rate"] == pytest.approx(1 / 2)


def test_build_failure_counts_every_dimension_wrong():
    expected = _build_expected({"anxiety": [70, 95]}, ["openness"])
    failed = {"status": "error", "mode": "model", "error": "timeout"}

    metrics = _by_name(qm.build_run_metrics([(BUILD_INPUT, failed, expected)]))

    assert metrics["build_dimension_judgment_accuracy"] == 0.0
    assert metrics["build_failure_rate"] == 1.0


# ── 실제 평가셋과의 호환 ───────────────────────────────


@pytest.mark.parametrize("filename", ["persona_onboarding_tagging.jsonl", "persona_build.jsonl"])
def test_metrics_run_on_every_dataset_row(filename):
    """정답 형식이 바뀌어 지표 계산이 깨지면 여기서 먼저 알 수 있다."""

    rows = [json.loads(line) for line in (DATASETS / filename).read_text().splitlines() if line.strip()]
    empty = {"status": "ok", "mode": "model", "result": {"primary": [], "secondary": [], "raw": {}}}

    metrics = qm.run_metrics([(row["input"], empty, row["expectedOutput"]) for row in rows])

    assert metrics
    assert all(0.0 <= m.value <= 1.0 for m in metrics)
