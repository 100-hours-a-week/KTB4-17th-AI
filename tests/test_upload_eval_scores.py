"""평가 결과 → Langfuse 점수 변환. 네트워크 없음."""

import json
from contextlib import contextmanager

import pytest

from scripts.upload_eval_scores import ENVIRONMENT, build_report, main, upload

SECRET_TEXT = "연락처 010 비밀 답변"

TAGGING = {
    "model": "tagging-v1",
    "splits": ["regression"],
    "summary": {"cases": 2, "exact_pct": 50.0, "failure_pct": 0.0, "latency_p95_s": 1.2, "by_category": {"direct": {}}},
    "results": [
        {
            "case_id": "tag-1",
            "category": "direct",
            "output": {"primary": [SECRET_TEXT]},
            "score": {"exact": True, "failed": False},
            "latency_s": 0.5,
        },
        {
            "case_id": "tag-2",
            "category": "direct",
            "output": None,
            "score": {"exact": False, "failed": True},
            "latency_s": 1.2,
        },
    ],
}

STAGE5 = {
    "feature": "f03-persona-extraction",
    "split": "regression",
    "version": "abc1234",
    "judge_model": "sonnet",
    "rows": [
        {
            "case_id": "f03-1",
            "split": "regression",
            "target_failures": ["F-02"],
            "sample": {"output_raw": SECRET_TEXT, "model": "extraction-v1", "fallback_used": False},
            "judge": {"F-02": {"verdict": "FAIL", "reasoning": SECRET_TEXT}},
            "verdict": {"F-02": "FAIL", "F-03": "PASS", "F-05": "NA"},
        },
        {
            "case_id": "f03-2",
            "split": "regression",
            "target_failures": [],
            "sample": {
                "output_raw": None,
                "model": "google/gemini-2.5-flash-lite",
                "fallback_used": True,
                "error": "x",
            },
            "judge": {},
            "verdict": {"F-02": "PASS", "F-03": "PASS", "F-05": "NA"},
        },
    ],
}


def _scores(items):
    return {item.name: (item.value, item.data_type) for item in items}


def test_tagging_result_becomes_boolean_case_scores_and_numeric_summary():
    report = build_report(TAGGING, run="tagging-v1")

    assert report.kind == "tagging"
    assert _scores(report.cases[0].scores) == {
        "tagging.exact": (1, "BOOLEAN"),
        "tagging.failed": (0, "BOOLEAN"),
        "tagging.latency_s": (0.5, "NUMERIC"),
    }
    assert _scores(report.summary) == {
        "tagging.exact_pct": (50.0, "NUMERIC"),
        "tagging.failure_pct": (0.0, "NUMERIC"),
        "tagging.latency_p95_s": (1.2, "NUMERIC"),
    }
    assert report.metadata["model"] == "tagging-v1"


def test_stage5_result_keeps_verdicts_fallback_and_na_out_of_fail_rate():
    report = build_report(STAGE5, run="f03-v1")

    assert report.kind == "f03-persona-extraction"
    first, second = report.cases
    assert _scores(first.scores)["verdict.F-02"] == ("FAIL", "CATEGORICAL")
    assert _scores(first.scores)["verdict.F-05"] == ("NA", "CATEGORICAL")
    assert _scores(first.scores)["fallback_used"] == (0, "BOOLEAN")
    assert _scores(second.scores)["fallback_used"] == (1, "BOOLEAN")
    assert _scores(second.scores)["app_error"] == (1, "BOOLEAN")
    assert first.metadata["model"] == "extraction-v1"

    summary = _scores(report.summary)
    assert summary["fail_rate.F-02"] == (0.5, "NUMERIC")
    assert summary["fail_rate.F-03"] == (0.0, "NUMERIC")
    assert "fail_rate.F-05" not in summary  # 판정한 케이스가 없으면 0% 가 아니라 없음이다
    assert summary["fail_count.F-02"] == (1, "NUMERIC")
    assert summary["fallback_rate"] == (0.5, "NUMERIC")
    assert summary["app_error_count"] == (1, "NUMERIC")


@pytest.mark.parametrize(("data", "run"), [(TAGGING, "a"), (STAGE5, "b")])
def test_report_never_contains_user_text(data, run):
    report = build_report(data, run=run)
    payload = [report.metadata, *[(case.metadata, case.scores) for case in report.cases], report.summary]
    assert SECRET_TEXT not in repr(payload)


def test_unknown_result_format_is_rejected():
    with pytest.raises(SystemExit, match="결과 형식"):
        build_report({"cases": []}, run="x")


class FakeClient:
    def __init__(self):
        self.observations = []
        self.scores = []

    @contextmanager
    def start_as_current_observation(self, **kwargs):
        self.observations.append(kwargs)
        yield

    def create_score(self, **kwargs):
        self.scores.append(kwargs)


def test_upload_is_idempotent_and_separated_by_run():
    first, again, other = FakeClient(), FakeClient(), FakeClient()
    upload(first, build_report(STAGE5, run="f03-v1"))
    upload(again, build_report(STAGE5, run="f03-v1"))
    upload(other, build_report(STAGE5, run="f03-v2"))

    assert first.scores == again.scores  # 같은 ID 라 다시 올려도 덮어쓴다
    assert len({score["score_id"] for score in first.scores}) == len(first.scores)
    assert not {s["score_id"] for s in first.scores} & {s["score_id"] for s in other.scores}
    assert all(score["environment"] == ENVIRONMENT for score in first.scores)

    case_scores = [score for score in first.scores if "trace_id" in score]
    run_scores = [score for score in first.scores if "session_id" in score]
    assert {score["trace_id"] for score in case_scores} == {
        observation["trace_context"]["trace_id"] for observation in first.observations
    }
    assert {score["session_id"] for score in run_scores} == {"eval-f03-v1"}
    assert not any("trace_id" in score for score in run_scores)


def test_cli_requires_keys_unless_dry_run(tmp_path, monkeypatch, capsys):
    path = tmp_path / "tagging-v1.json"
    path.write_text(json.dumps(TAGGING), encoding="utf-8")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")

    assert main([str(path), "--dry-run"]) == 0
    assert "eval-tagging-v1" in capsys.readouterr().out

    with pytest.raises(SystemExit, match="LANGFUSE_PUBLIC_KEY"):
        main([str(path)])
