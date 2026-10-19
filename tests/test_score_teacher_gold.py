"""골드셋 채점 — 손으로 만든 teacher 출력으로 지표 계산을 확인한다."""

import json

from scripts.score_teacher_gold import (
    disagreements,
    grounded,
    load_gold,
    markdown_table,
    narrative_checks,
    score_run,
)


def _tag_row(case, primary, off=False, agreement=1.0):
    maj = {"primary": primary, "secondary": [], "off_topic": off}
    sample = {**maj, "invalidDimensions": [], "extraKeys": []}
    return {
        "id": f"gold:persona_onboarding_tagging-{case}",
        "samples": [sample] * 5,
        "aggregate": {"validSamples": 5, "majority": maj, "agreement": agreement},
    }


USER = [
    {"role": "assistant", "content": "요즘 모으는 것이 있다고요?"},
    {"role": "user", "content": "오래된 지도예요. 주말 아침에 지명의 변화를 찾아봐요."},
    {"role": "assistant", "content": "만나는 사람이 그 시간을 함께 보내자고 하면요?"},
    {"role": "user", "content": "지도 보는 오전만큼은 혼자 있고 싶어요. 지난 일요일에도 오후에 만나자고 했죠."},
]


def _write_run(tmp_path, name, tag_rows, extract_rows):
    d = tmp_path / name
    d.mkdir()
    (d / "tagging.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in tag_rows))
    (d / "extraction.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in extract_rows))
    (d / "summary.json").write_text(json.dumps({"teacher": {"model": name}, "estimatedCostUsd": 0.1}))
    return d


def _extract_row(output):
    return {
        "id": "gold:persona_build-001",
        "input": {"history": USER, "answered": ["avoidance", "interests", "routine"]},
        "output": output,
    }


def test_tagging_metrics(tmp_path):
    gold_tags, gold_builds = load_gold()
    rows = [
        _tag_row("001", ["avoidance"]),  # 정답
        _tag_row("002", ["routine"]),  # 오답(정답 avoidance), 5회 모두 같음 → 일관된 오답
        _tag_row("003", [], off=False),  # 정답 off_topic=true 를 놓침
        _tag_row("007", []),  # 근거 부족 답에서 유보 — 정답
        _tag_row("011", ["avoidance"]),  # 사람 합의 대기 사례 — 본 분모에서 제외
    ]
    run = _write_run(tmp_path, "a", rows, [])
    t = score_run(run, gold_tags, gold_builds)["tagging"]
    assert t["items"] == 4
    assert t["heldForReview"]["items"] == 1
    assert t["primaryPrecision"] == 0.5  # tp 1 (001), fp 1 (002 routine)
    assert t["primaryRecall"] == 0.5  # fn 1 (002 avoidance)
    assert t["exactMatch"] == 0.5
    assert t["offTopicHits"] == "0/1"
    assert t["abstentions"] == "1/1"
    assert t["consistentWrongIds"] == ["gold:persona_onboarding_tagging-002", "gold:persona_onboarding_tagging-003"]


def test_extraction_dimension_decisions(tmp_path):
    gold_tags, gold_builds = load_gold()
    output = {"avoidance": 85, "anxiety": 40, "interests": ["고지도"], "routine": [], "date_prefer": ["산책"]}
    run = _write_run(tmp_path, "b", [], [_extract_row(output)])
    e = score_run(run, gold_tags, gold_builds)["extraction"]
    assert e["decisions"] == {"tp": 1, "fp": 1, "tn": 13}
    assert e["inRange"] == "1/1"
    assert e["hallucinationRate"] == round(1 / 14, 4)
    assert e["missRate"] == 0.0
    # interests 유지 ✓, routine 비어 있음 ✗, date_prefer 생략해야 하는데 냄 ✗, date_avoid 생략 ✓
    assert e["textualDecisionAccuracy"] == 0.5


def test_extraction_out_of_range_and_missing(tmp_path):
    gold_tags, gold_builds = load_gold()
    out_of_range = score_run(_write_run(tmp_path, "c", [], [_extract_row({"avoidance": 30})]), gold_tags, gold_builds)
    assert out_of_range["extraction"]["decisions"]["tp_out"] == 1
    missing = score_run(_write_run(tmp_path, "d", [], [_extract_row({})]), gold_tags, gold_builds)
    assert missing["extraction"]["missRate"] == 1.0


def test_narrative_checks():
    good = {
        "avoidance": 85,
        "narrative": {
            "headline": "함께하되 혼자만의 오전은 지키는 타입",
            "body": "주말 아침에 지명의 변화를 찾아본다고 했어요. 지도 보는 오전만큼은 혼자 있고 싶다고 했어요.",
            "traits": ["주말 아침엔 지도를 봄", "오전은 혼자 보내는 편", "개인 시간을 지킴"],
        },
    }
    checks = narrative_checks(good, USER, {"avoidance"})
    assert checks == {
        "valid": True,
        "noSpeculation": True,
        "noContradiction": True,
        "lengthContract": True,
        "quoteGrounded": True,
    }
    bad = json.loads(json.dumps(good))
    bad["narrative"]["body"] += " 혼자 있는 걸 좋아하실 것 같아요."
    checks = narrative_checks(bad, USER, {"avoidance"})
    assert not checks["noSpeculation"] and not checks["quoteGrounded"]
    assert narrative_checks({"avoidance": 85}, USER, {"avoidance"}) is None


def test_grounded():
    assert grounded("지도 보는 오전만큼은 혼자라고 했어요.", "지도 보는 오전만큼은 혼자 있고 싶어요.")
    assert not grounded("함께 있는 시간을 중시해요.", "지도 보는 오전만큼은 혼자 있고 싶어요.")


def test_comparison_table_and_disagreements(tmp_path):
    gold_tags, gold_builds = load_gold()
    a = score_run(_write_run(tmp_path, "a", [_tag_row("001", ["avoidance"])], []), gold_tags, gold_builds)
    b = score_run(_write_run(tmp_path, "b", [_tag_row("001", ["withdrawal"])], []), gold_tags, gold_builds)
    table = markdown_table([a, b])
    assert "| 태깅 primary F1 | 1.0 | 0.0 |" in table
    diffs = disagreements([a, b], gold_tags)
    assert len(diffs) == 1 and diffs[0]["runs"]["a"].startswith("✓")
