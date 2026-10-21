"""평가 스크립트 지표 계산 — 실제 실행 없이 실행 기록 → 9절 지표."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "eval_v2_simulation", Path(__file__).resolve().parents[2] / "scripts" / "eval_v2_simulation.py"
)
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)


def _run(**kw):
    base = dict(
        kind="hard",
        status="succeeded",
        reason=None,
        elapsed_s=20.0,
        llm_calls=3,
        mode="shared",
        final_issue_types=[],
        line_count=20,
        echo_lines=0,
        early_farewell=False,
        effort_ratio=1.0,
        planned_topics=[[1, 11], [2, 12]],
        used_topic_refs=[1, 11, 2],
    )
    return {**base, **kw}


def test_summarize_metrics():
    runs = [
        _run(),
        _run(status="failed", reason="speaker_mixup_unresolved", llm_calls=7, elapsed_s=60.0),
        _run(kind="normal", echo_lines=2, early_farewell=True, effort_ratio=0.5, used_topic_refs=[1, 11]),
        _run(kind="normal", final_issue_types=["self_address"]),
    ]
    out = ev.summarize(runs)
    assert out["runs"] == 4
    assert out["failure_rate"] == 0.25
    assert out["mixup_rate_hard"] == 0.5
    assert out["mixup_rate_normal"] == 0.5
    assert out["echo_rate"] == round(2 / 60, 4)
    assert out["early_farewell_rate"] == round(1 / 3, 4)
    assert out["effort_ratio_median"] == 1.0
    assert out["topic_use_rate"] == round(2 / 3, 4)
    assert out["llm_calls_max"] == 7 and out["llm_calls_mean"] == 4.0
    assert out["elapsed_p50"] == 20.0


def test_summarize_empty_and_effort_ratio():
    assert ev.summarize([]) == {"runs": 0}
    assert ev.effort_ratio(["aaaa"] * 7 + ["aa"] * 3) == 0.5
    assert ev.effort_ratio(["a"]) is None


def test_planned_topics_parsed_from_trace_note():
    trace = [{"node": "plan_topics", "note": "mode=shared exposed=2+2 topics=1+11|2"}]
    assert ev._planned_topics(trace) == [[1, 11], [2]]
    assert ev._planned_topics([]) == []
