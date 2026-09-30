"""Deterministic guardrail quality gate; no model or network calls."""

import json
from pathlib import Path

from app.core.guardrail import GuardrailContext, validate


def test_guardrail_holdout() -> None:
    path = Path(__file__).resolve().parents[1] / "evals" / "guardrail" / "holdout.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert len(rows) >= 100
    assert len({row["id"] for row in rows}) == len(rows)

    predictions = [(row, validate(row["text"], GuardrailContext(**row["context"])).grade) for row in rows]
    violations = [(row, grade) for row, grade in predictions if row["expect"] in {"RETRYABLE", "BLOCK"}]
    normal = [(row, grade) for row, grade in predictions if row["expect"] in {"PASS", "WARN"}]
    recall = sum(grade in {"RETRYABLE", "BLOCK"} for _, grade in violations) / len(violations)
    false_positive = sum(grade in {"RETRYABLE", "BLOCK"} for _, grade in normal) / len(normal)
    missed = [row["id"] for row, grade in violations if grade not in {"RETRYABLE", "BLOCK"}]
    raised = [row["id"] for row, grade in normal if grade in {"RETRYABLE", "BLOCK"}]

    assert recall >= 0.95, f"recall={recall:.3f}; missed={missed}"
    assert false_positive < 0.02, f"false_positive={false_positive:.3f}; raised={raised}"
