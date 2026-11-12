"""태깅 파인튜닝 데이터 검증·변환과 평가 채점. 학습 자체(torch)는 GPU 서버에서 돌리므로 여기선 다루지 않는다."""

import json

import pytest

from app.features.persona.agents import TAG_PROMPT, parse_tags, tagging_messages
from scripts.eval_tagging import _arguments, load_cases, score, summarize
from training.tagging.build_sft import is_validation, main, to_record
from training.tagging.data import TagExample, eval_answers, normalize, validate


def example(**overrides) -> TagExample:
    fields = {
        "id": "t-1",
        "question": "연락은 얼마나 자주 하세요?",
        "answer": "하루 한두 번이면 충분해요.",
        "primary": ["contact_rhythm"],
        "source": "team_written",
        "reviewed_by": "lena",
    } | overrides
    return TagExample(**fields)


def test_training_prompt_is_exactly_what_serving_sends():
    record = to_record(example())

    assert record["prompt"][0] == {"role": "system", "content": TAG_PROMPT}
    assert record["prompt"][1:] == tagging_messages("연락은 얼마나 자주 하세요?", "하루 한두 번이면 충분해요.")
    completion = json.loads(record["completion"][0]["content"])
    assert completion == {"primary": ["contact_rhythm"], "secondary": [], "off_topic": False}
    # 학습 정답은 서빙 파서를 그대로 통과해야 한다
    assert parse_tags(completion).primary == ["contact_rhythm"]


def test_valid_example_passes():
    assert validate([example()], leaked=set()) == []


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"primary": ["made_up"]}, "등록되지 않은 차원"),
        ({"secondary": ["contact_rhythm"]}, "같은 차원"),
        ({"primary": ["routine", "routine"]}, "중복"),
        ({"off_topic": True}, "off_topic"),
        ({"reviewed_by": None}, "reviewed_by"),
        ({"source": "production_consented"}, "consent_basis"),
    ],
)
def test_invalid_examples_are_rejected(overrides, message):
    errors = validate([example(**overrides)], leaked=set())

    assert any(message in error for error in errors), errors


def test_unreviewed_is_allowed_only_when_asked():
    assert validate([example(reviewed_by=None)], leaked=set(), allow_unreviewed=True) == []


def test_eval_answers_leaking_into_training_are_caught_despite_spacing():
    leaked_answer = load_cases(["regression"])[0]["input"]["answer"]
    errors = validate([example(answer=f"  {leaked_answer}!! ")])

    assert any("평가 누수" in error for error in errors)
    assert normalize(leaked_answer) in eval_answers()


def test_duplicate_ids_and_pairs_are_rejected():
    errors = validate([example(), example(answer="하루 한두 번이면 충분해요!!")], leaked=set())

    assert any("id 중복" in error for error in errors)
    assert any("같은 질문·답변" in error for error in errors)


def test_validation_split_is_stable_per_id():
    assert is_validation("t-1", 0.5) == is_validation("t-1", 0.5)
    assert sum(is_validation(f"t-{i}", 0.2) for i in range(1000)) in range(150, 250)


def _build(tmp_path, examples: list[TagExample], *extra: str) -> tuple[dict, dict[str, list[str]]]:
    src = tmp_path / "examples.jsonl"
    src.write_text("".join(ex.model_dump_json() + "\n" for ex in examples), encoding="utf-8")
    out = tmp_path / "out"
    assert main([str(src), "--out", str(out), "--val-ratio", "0.5", *extra]) == 0
    ids = {
        name: [json.loads(line)["id"] for line in (out / f"{name}.jsonl").read_text().splitlines()]
        for name in ("train", "val")
    }
    return json.loads((out / "manifest.json").read_text()), ids


def test_split_keeps_each_session_on_one_side(tmp_path):
    # 같은 사람의 다른 답이 train 에 있으면 val 이 처음 보는 사용자를 재지 못한다
    examples = [
        example(id=f"s{s}-t{t}", answer=f"세션 {s} 의 {t}번째 답이에요", group=f"seed-{s}")
        for s in range(20)
        for t in range(3)
    ]
    _, ids = _build(tmp_path, examples)

    side = {i: name for name, rows in ids.items() for i in rows}
    for s in range(20):
        assert len({side[f"s{s}-t{t}"] for t in range(3)}) == 1
    assert ids["train"] and ids["val"]


def test_max_primary_drops_over_tagged_examples(tmp_path):
    examples = [
        example(id="one", answer="답 하나", primary=["routine"]),
        example(id="three", answer="답 셋", primary=["routine", "interests", "anxiety"]),
    ]
    manifest, ids = _build(tmp_path, examples, "--max-primary", "2")

    assert sorted(ids["train"] + ids["val"]) == ["one"]
    assert manifest["dropped_by_max_primary"] == 1 and manifest["max_primary"] == 2


EXPECTED = {
    "autoMetrics": {
        "primary": {"accepted": [["avoidance"]]},
        "secondary": {"accepted": [[], ["routine"]]},
        "off_topic": {"accepted": [False]},
    }
}


def test_score_accepts_any_listed_alternative_regardless_of_order():
    result = score({"primary": ["avoidance"], "secondary": ["routine"], "off_topic": False}, EXPECTED)

    assert result == {"primary": True, "secondary": True, "off_topic": True, "exact": True, "failed": False}


def test_score_marks_partial_and_failed_outputs():
    partial = score({"primary": ["avoidance", "anxiety"], "secondary": [], "off_topic": False}, EXPECTED)
    failed = score(None, EXPECTED)

    assert partial["primary"] is False and partial["exact"] is False and partial["secondary"] is True
    assert failed["failed"] is True and failed["exact"] is False


def test_summary_reports_off_topic_counts_and_failures():
    rows = [
        {"category": "direct", "expected_off_topic": False, "latency_s": 0.5, "score": score(None, EXPECTED)},
        {
            "category": "off_topic",
            "expected_off_topic": True,
            "latency_s": 1.0,
            "score": {"primary": True, "secondary": True, "off_topic": True, "exact": True, "failed": False},
        },
    ]
    summary = summarize(rows)

    assert summary["exact_pct"] == 50.0
    assert summary["failure_pct"] == 50.0
    assert summary["off_topic_positive"] == {"cases": 1, "caught": 1}


def test_blind_holdout_needs_explicit_flag():
    with pytest.raises(SystemExit):
        _arguments(["--split", "blind_holdout"])
    assert _arguments(["--split", "blind_holdout", "--allow-holdout"]).split == ["blind_holdout"]


def test_fault_injection_case_is_not_label_scored():
    cases = load_cases(["calibration", "regression", "blind_holdout"])

    assert len(cases) == 79
    assert all(case["expectedOutput"]["autoMetrics"]["applies"] for case in cases)
