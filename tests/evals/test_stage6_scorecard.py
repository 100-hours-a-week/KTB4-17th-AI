import json

from evals._harness import stage6_scorecard as S


def test_scorecard_na_error_incomplete_not_pass(tmp_path):
    # NA, ERROR, 누락된 유형은 절대 PASS 가 아님을 검증
    res_file = tmp_path / "test_result.json"
    rows = [
        # 케이스 1: 실행 오류 발생
        {
            "case_id": "c1",
            "split": "regression",
            "sample": {"error": "connection timeout", "output_raw": None},
            "verdict": {"F-01": "PASS"},
        },
        # 케이스 2: NA 포함 (필수 유형은 채워져 있음)
        {
            "case_id": "c2",
            "split": "regression",
            "sample": {"output_raw": "출력"},
            "verdict": {**{t: "PASS" for t in S.get_all_defined_types("f01")}, "F-01": "NA"},
        },
        # 케이스 3: 정상 PASS
        {
            "case_id": "c3",
            "split": "regression",
            "sample": {"output_raw": "정상"},
            "verdict": {t: "PASS" for t in S.get_all_defined_types("f01")},
        },
    ]
    res_file.write_text(json.dumps({"feature": "f01", "split": "regression", "rows": rows}))

    res = S.parse_and_score(res_file)
    card = res["scorecard"]

    assert card.total_cases == 3
    # 오직 c3 만 all pass 여야 함
    assert card.observed_all_pass_cases == 1
    # c1 은 incomplete/error 로 분류되어 pass 로 카운트되지 않음
    assert card.observed_incomplete_cases >= 1
    # c2 는 NA 포함으로 분류되어 pass 로 카운트되지 않음
    assert card.observed_na_cases >= 1


def test_scorecard_sd_crit_types_blocks_gate1(tmp_path):
    # S.D 원본의 sev='CRIT' 유형이 실패하면 Gate1 즉시 FAIL
    res_file = tmp_path / "test_crit_result.json"
    crit_types = S.get_feature_crit_types("f01")
    assert "F-08" in crit_types  # F-08 은 CRIT

    rows = [
        {
            "case_id": "c1",
            "split": "regression",
            "sample": {"output_raw": "출력"},
            "verdict": {"F-08": "FAIL"},  # CRIT 실패
        }
    ]
    res_file.write_text(json.dumps({"feature": "f01", "split": "regression", "rows": rows}))

    res = S.parse_and_score(res_file)
    gates = {g.gate_id: g for g in res["gates"]}

    assert gates["Gate1"].passed is False
    assert gates["Gate1"].status == "FAIL"
    assert "치명 결함(CRIT)" in gates["Gate1"].details[0]


def test_scorecard_gates_unknown_without_evidence(tmp_path):
    # 확정 증거가 없으면 Gate 0~3 이 UNKNOWN 으로 보류되는지 검증
    res_file = tmp_path / "test_unknown_result.json"
    rows = [
        {
            "case_id": "c1",
            "split": "regression",
            "sample": {"output_raw": "출력"},
            "verdict": {t: "PASS" for t in S.get_all_defined_types("f01")},
        }
    ]
    res_file.write_text(json.dumps({"feature": "f01", "split": "regression", "rows": rows}))

    res = S.parse_and_score(res_file)
    gates = {g.gate_id: g for g in res["gates"]}

    # 원문/제품 분리 증거, 멱등성/동시성, 정성 루브릭 미관측으로 UNKNOWN 처리
    assert gates["Gate1"].status == "UNKNOWN"
    assert gates["Gate2"].status == "UNKNOWN"
    assert gates["Gate3"].status == "UNKNOWN"


def test_scorecard_empty_rows_safe(tmp_path):
    res_file = tmp_path / "empty.json"
    res_file.write_text(json.dumps({"feature": "f01", "split": "regression", "rows": []}))
    res = S.parse_and_score(res_file)
    assert res["scorecard"].total_cases == 0
    for g in res["gates"]:
        assert g.status == "UNKNOWN"
