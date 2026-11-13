"""태깅 blind_holdout_v2 — 형식·누수 검사를 통과하고 eval_tagging 이 그대로 읽는다."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cases_pass_format_and_leak_check():
    builder = _load("build_tagging_holdout_v2")

    assert builder.check() == []
    assert len(builder.CASES) == 75


def test_built_file_is_scored_only_with_allow_holdout():
    builder, evaluator = _load("build_tagging_holdout_v2"), _load("eval_tagging")
    rows = [json.loads(line) for line in builder.OUT.read_text(encoding="utf-8").splitlines()]

    cases = evaluator.load_cases(["blind_holdout"], builder.OUT)

    assert len(cases) == len(builder.CASES) == len(rows)
    assert {r["metadata"]["split"] for r in rows} == {"blind_holdout"}
    first = rows[0]["expectedOutput"]
    assert evaluator.score({"primary": ["interests"], "secondary": [], "off_topic": False}, first)["primary"]
