"""5단계 Judge 보정 집계 무결성 및 거짓 통과(False Pass) 차단 단위 테스트 (tests/evals/test_stage5_labels_integrity.py).

검증 항목:
1. root 재현 버그: 유효 정답 대비 기계 ERROR 혼합 시 통과(✅) 차단 및 UNKNOWN/NOT_VALIDATED 보류
2. 유효 정답의 결측/NA/누락/알수없는판정/실행오류/잘림 발생 시 성능 PASS 금지 및 사유 분리
3. 프롬프트 provenance 부재(역사적 관측치) 및 해시 불일치 시 통과 금지
4. f09 F-01 참조 라벨 충돌 격리 (QUARANTINE_LABEL_CONFLICT) 및 원본 불변
5. f10/f11 제외 슬러그의 J.folder() 조회 0회 및 미존재 환경 무장애 통과
6. 정상 완전 채점 + 양 클래스 표본 존재 + 정합 provenance 일 때만 수학상 통과
"""

from __future__ import annotations

import copy
import hashlib
from collections import Counter

import pytest

import evals._harness.stage3_types as S
import evals._harness.stage5_judges as J
import evals._harness.stage5_labels as L

# ── 1. Root 재현 버그 검증 ──────────────────────────────────────────


def test_root_reproduced_bug_blocks_false_pass_and_reports_quantities(monkeypatch):
    """root가 재현한 버그: gold(fail=FAIL, pass=PASS, error=PASS), result(fail=FAIL, pass=PASS, error=ERROR).

    기존 로직은 err_count를 제외하고 TPR 1.0, TNR 1.0으로 '✅ 통과'를 내던 결함.
    수정 후:
    - 절대 '✅' 또는 '통과'가 되지 않음
    - UNKNOWN / NOT_VALIDATED 상태로 보류
    - 기계 ERROR 1건 사유 표기
    - 전체 필요수(3), 채점 가능수(2), 누락/보류(1) 수량 분리
    """
    mock_types = [{"id": "F-01", "name": "자기소개 착각", "sev": "중대", "defn": "defn", "fail": "fail"}]
    mock_d = {"f01": {"types": mock_types}}
    monkeypatch.setattr(S, "D", mock_d)

    mock_gold = {
        "fail": {"F-01": "FAIL"},
        "pass": {"F-01": "PASS"},
        "error": {"F-01": "PASS"},
    }
    monkeypatch.setattr(L, "read_labels", lambda path: mock_gold)

    mock_res = {
        "version": "test-v1",
        "run_at": "2026-10-28T12:00:00",
        "rows": [
            {"case_id": "fail", "sample": {}, "verdict": {"F-01": "FAIL"}},
            {"case_id": "pass", "sample": {}, "verdict": {"F-01": "PASS"}},
            {"case_id": "error", "sample": {}, "verdict": {"F-01": "ERROR"}},
        ],
        "rejudge_provenance": {
            "prompt_hashes": {"F-01": "mockhash12345678"},
            "type_provenance": {"F-01": {"prompt_hash": "mockhash12345678", "status": "rejudged"}},
        },
    }
    monkeypatch.setattr(L, "latest_result", lambda slug: mock_res)
    monkeypatch.setattr(L, "compute_prompt_hash", lambda slug, tid: "mockhash12345678")

    reports = L.calib(write_eval_md=False)
    rep = reports["f01"]

    # 1. 절대 통과가 아니어야 함
    assert "✅ 통과" not in rep
    assert "UNKNOWN/NOT_VALIDATED" in rep

    # 2. 구체 누락 건수 및 사유(기계 ERROR 1건) 표기
    assert "기계 ERROR 1건" in rep
    assert "error(기계 ERROR)" in rep

    # 3. 전체 필요수(3)와 채점 가능수(2) 분리 확인
    # 표 행: | F-01 | Judge | 3 | 2 | 1 | 1 | 1.00 | 1.00 | ⚠️ UNKNOWN/NOT_VALIDATED (기계 ERROR 1건) |
    assert "| F-01 | Judge | 3 | 2 | 1 | 1 | 1.00 | 1.00 |" in rep


# ── 2. 결측 / NA / 누락 / 알 수 없는 판정 / 실행오류 / 잘림 차단 검증 ──


@pytest.mark.parametrize(
    ("corrupt_type", "expected_reason"),
    [
        ("judge_na", "기계 NA"),
        ("missing_verdict", "판정 결측"),
        ("missing_case", "케이스 누락"),
        ("unknown_verdict", "알 수 없는 판정"),
        ("execution_error", "실행 오류"),
        ("input_truncated", "입출력 잘림"),
    ],
)
def test_unvalidated_cases_block_pass_with_exact_reason(monkeypatch, corrupt_type, expected_reason):
    """유효 정답 케이스에 NA, 판정 결측, 케이스 누락, 알수없는 판정, 실행오류, 잘림 발생 시 통과 금지 및 사유 명시."""
    mock_types = [{"id": "F-01", "name": "자기소개 착각", "sev": "중대", "defn": "defn", "fail": "fail"}]
    monkeypatch.setattr(S, "D", {"f01": {"types": mock_types}})

    mock_gold = {
        "c_fail": {"F-01": "FAIL"},
        "c_pass": {"F-01": "PASS"},
        "c_corrupt": {"F-01": "PASS"},
    }
    monkeypatch.setattr(L, "read_labels", lambda path: mock_gold)

    corrupt_row: dict = {"case_id": "c_corrupt", "sample": {}, "verdict": {"F-01": "PASS"}}
    if corrupt_type == "judge_na":
        corrupt_row["verdict"]["F-01"] = "NA"
    elif corrupt_type == "missing_verdict":
        corrupt_row["verdict"] = {}
    elif corrupt_type == "unknown_verdict":
        corrupt_row["verdict"]["F-01"] = "UNCERTAIN_CUSTOM_STRING"
    elif corrupt_type == "execution_error":
        corrupt_row["sample"]["error"] = "HTTP 503 Backend Service Unavailable"
    elif corrupt_type == "input_truncated":
        corrupt_row["truncation_warnings"] = {"F-01": True}

    rows = [
        {"case_id": "c_fail", "sample": {}, "verdict": {"F-01": "FAIL"}},
        {"case_id": "c_pass", "sample": {}, "verdict": {"F-01": "PASS"}},
    ]
    if corrupt_type != "missing_case":
        rows.append(corrupt_row)

    mock_res = {
        "version": "test-v1",
        "rows": rows,
        "rejudge_provenance": {
            "prompt_hashes": {"F-01": "mockhash12345678"},
            "type_provenance": {"F-01": {"prompt_hash": "mockhash12345678", "status": "rejudged"}},
        },
    }
    monkeypatch.setattr(L, "latest_result", lambda slug: mock_res)
    monkeypatch.setattr(L, "compute_prompt_hash", lambda slug, tid: "mockhash12345678")

    rep = L.calib(write_eval_md=False)["f01"]

    assert "✅ 통과" not in rep
    assert "UNKNOWN/NOT_VALIDATED" in rep
    assert expected_reason in rep


# ── 3. 프롬프트 Provenance 무결성 검증 ───────────────────────────────


def test_provenance_absence_blocks_pass_as_historical_observation(monkeypatch):
    """과거 결과에 rejudge_provenance가 없으면 역사적 관측치로 보고하고 통과 금지."""
    mock_types = [{"id": "F-01", "name": "자기소개 착각", "sev": "중대", "defn": "defn", "fail": "fail"}]
    monkeypatch.setattr(S, "D", {"f01": {"types": mock_types}})

    mock_gold = {
        "c_fail": {"F-01": "FAIL"},
        "c_pass": {"F-01": "PASS"},
    }
    monkeypatch.setattr(L, "read_labels", lambda path: mock_gold)

    # provenance 필드가 전혀 없는 과거 기준선 결과 모킹
    mock_res = {
        "version": "historical-cb53097",
        "rows": [
            {"case_id": "c_fail", "sample": {}, "verdict": {"F-01": "FAIL"}},
            {"case_id": "c_pass", "sample": {}, "verdict": {"F-01": "PASS"}},
        ],
    }
    monkeypatch.setattr(L, "latest_result", lambda slug: mock_res)
    monkeypatch.setattr(L, "compute_prompt_hash", lambda slug, tid: "mockhash12345678")

    rep = L.calib(write_eval_md=False)["f01"]
    assert "✅ 통과" not in rep
    assert "UNKNOWN/NOT_VALIDATED (역사적 관측치: 프롬프트 provenance 부재)" in rep


def test_provenance_hash_mismatch_blocks_pass(monkeypatch):
    """결과 파일의 prompt_hash와 현재 판정 프롬프트 해시가 불일치하면 통과 금지."""
    mock_types = [{"id": "F-01", "name": "자기소개 착각", "sev": "중대", "defn": "defn", "fail": "fail"}]
    monkeypatch.setattr(S, "D", {"f01": {"types": mock_types}})

    mock_gold = {
        "c_fail": {"F-01": "FAIL"},
        "c_pass": {"F-01": "PASS"},
    }
    monkeypatch.setattr(L, "read_labels", lambda path: mock_gold)

    mock_res = {
        "version": "test-v1",
        "rows": [
            {"case_id": "c_fail", "sample": {}, "verdict": {"F-01": "FAIL"}},
            {"case_id": "c_pass", "sample": {}, "verdict": {"F-01": "PASS"}},
        ],
        "rejudge_provenance": {
            "prompt_hashes": {"F-01": "old_hash_11111111"},
            "type_provenance": {"F-01": {"prompt_hash": "old_hash_11111111", "status": "rejudged"}},
        },
    }
    monkeypatch.setattr(L, "latest_result", lambda slug: mock_res)
    # 현재 디스크 프롬프트 해시는 변경됨
    monkeypatch.setattr(L, "compute_prompt_hash", lambda slug, tid: "new_hash_22222222")

    rep = L.calib(write_eval_md=False)["f01"]
    assert "✅ 통과" not in rep
    assert "UNKNOWN/NOT_VALIDATED (프롬프트 provenance 불일치)" in rep


def test_provenance_historical_unmodified_blocks_pass(monkeypatch):
    """부분 재판정에서 해당 유형이 갱신되지 않은 historical_unmodified 상태면 통과 금지."""
    mock_types = [{"id": "F-01", "name": "자기소개 착각", "sev": "중대", "defn": "defn", "fail": "fail"}]
    monkeypatch.setattr(S, "D", {"f01": {"types": mock_types}})

    mock_gold = {
        "c_fail": {"F-01": "FAIL"},
        "c_pass": {"F-01": "PASS"},
    }
    monkeypatch.setattr(L, "read_labels", lambda path: mock_gold)

    mock_res = {
        "version": "test-v1",
        "rows": [
            {"case_id": "c_fail", "sample": {}, "verdict": {"F-01": "FAIL"}},
            {"case_id": "c_pass", "sample": {}, "verdict": {"F-01": "PASS"}},
        ],
        "rejudge_provenance": {
            "type_provenance": {"F-01": {"prompt_hash": "mockhash12345678", "status": "historical_unmodified"}},
        },
    }
    monkeypatch.setattr(L, "latest_result", lambda slug: mock_res)
    monkeypatch.setattr(L, "compute_prompt_hash", lambda slug, tid: "mockhash12345678")

    rep = L.calib(write_eval_md=False)["f01"]
    assert "✅ 통과" not in rep
    assert "UNKNOWN/NOT_VALIDATED (역사적 관측치: 프롬프트 provenance 부재)" in rep


def test_perfect_run_with_provenance_and_two_classes_passes(monkeypatch):
    """결측 0, 양쪽 클래스 표본 존재, provenance 완벽 일치, TPR/TNR >= 0.85일 때만 통과."""
    mock_types = [{"id": "F-01", "name": "자기소개 착각", "sev": "중대", "defn": "defn", "fail": "fail"}]
    monkeypatch.setattr(S, "D", {"f01": {"types": mock_types}})

    mock_gold = {f"fail_{i}": {"F-01": "FAIL"} for i in range(10)}
    mock_gold.update({f"pass_{i}": {"F-01": "PASS"} for i in range(10)})
    monkeypatch.setattr(L, "read_labels", lambda path: mock_gold)

    rows = [{"case_id": f"fail_{i}", "sample": {}, "verdict": {"F-01": "FAIL"}} for i in range(10)]
    rows.extend([{"case_id": f"pass_{i}", "sample": {}, "verdict": {"F-01": "PASS"}} for i in range(10)])

    mock_res = {
        "version": "test-v1",
        "rows": rows,
        "rejudge_provenance": {
            "prompt_hashes": {"F-01": "matching_hash123"},
            "type_provenance": {"F-01": {"prompt_hash": "matching_hash123", "status": "rejudged"}},
        },
    }
    monkeypatch.setattr(L, "latest_result", lambda slug: mock_res)
    monkeypatch.setattr(L, "compute_prompt_hash", lambda slug, tid: "matching_hash123")

    rep = L.calib(write_eval_md=False)["f01"]
    assert "✅ 통과" in rep
    assert "| F-01 | Judge | 20 | 20 | 10 | 10 | 1.00 | 1.00 | ✅ 통과 |" in rep


# ── 4. f09 F-01 격리 및 라벨 원본 불변 검증 ─────────────────────────


def test_f09_f01_quarantine_label_conflict_and_label_file_immutable(monkeypatch):
    """f09 F-01은 무조건 QUARANTINE_LABEL_CONFLICT로 보고되며, 라벨 원본 파일은 변경되지 않아야 함."""
    label_path = J.folder("f09") / "labels" / "calibration_final.csv"
    orig_bytes = label_path.read_bytes()
    orig_sha = hashlib.sha256(orig_bytes).hexdigest()

    # f09의 calib 실행
    reports = L.calib(write_eval_md=False)
    rep = reports["f09"]

    # F-01 격리 상태 검증
    assert "QUARANTINE_LABEL_CONFLICT" in rep
    assert "✅ 통과" not in rep.split("| F-01 |")[1].split("\n")[0]

    # 원본 파일 불변 검증
    current_sha = hashlib.sha256(label_path.read_bytes()).hexdigest()
    assert current_sha == orig_sha, "calibration_final.csv 원본 라벨 파일이 수정되어서는 안 됨"


# ── 5. f10/f11 제외 및 J.folder 0회 조회 검증 ────────────────────────


def test_f10_f11_zero_folder_calls(monkeypatch):
    """신규 평가 루프에서 f10/f11은 J.folder 호출 전에 제외되어 조회수가 0회여야 함."""
    folder_calls = Counter()
    orig_folder = J.folder

    def spy_folder(slug):
        folder_calls[slug] += 1
        return orig_folder(slug)

    monkeypatch.setattr(J, "folder", spy_folder)

    # calib 호출
    reports = L.calib(write_eval_md=False)

    assert "f10" not in reports
    assert "f11" not in reports
    assert folder_calls["f10"] == 0, f"f10에 대해 J.folder가 {folder_calls['f10']}회 호출됨"
    assert folder_calls["f11"] == 0, f"f11에 대해 J.folder가 {folder_calls['f11']}회 호출됨"

    # latest_result, goldset, compute_prompt_hash 단독 호출 검증
    assert L.latest_result("f10") is None
    assert L.latest_result("f11") is None
    assert L.goldset("f10") == {}
    assert L.goldset("f11") == {}
    assert L.compute_prompt_hash("f10", "F-01") is None
    assert L.compute_prompt_hash("f11", "F-01") is None

    assert folder_calls["f10"] == 0
    assert folder_calls["f11"] == 0


def test_calib_runs_cleanly_when_f10_f11_directories_do_not_exist(tmp_path, monkeypatch):
    """f10/f11 디렉토리가 아예 없는 클린 체크아웃 환경에서도 에러 없이 정상 동작 검증."""
    fake_evals = tmp_path / "clean_evals"
    fake_evals.mkdir()

    # f01 디렉토리만 생성
    f01_dir = fake_evals / "f01-persona-conversation"
    f01_dir.mkdir()
    (f01_dir / "labels").mkdir()
    (f01_dir / "results").mkdir()
    (f01_dir / "goldset.jsonl").write_text('{"id": "01", "input": {}}\n')
    (f01_dir / "labels" / "calibration_final.csv").write_text("case_id,F-01\n01,PASS\n")
    (f01_dir / "results" / "20261028-1200_test_calibration.json").write_text(
        '{"version": "v1", "rows": [{"case_id": "01", "sample": {}, "verdict": {"F-01": "PASS"}}]}\n'
    )

    monkeypatch.setattr(J, "EVALS", fake_evals)
    monkeypatch.setattr(L, "EVALS", fake_evals)

    # S.D에는 f10, f11이 포함되어 있는 상태
    d_with_f10 = copy.deepcopy(S.D)
    monkeypatch.setattr(S, "D", d_with_f10)

    # FileNotFoundError 없이 정상 완료되어야 함
    reports = L.calib(write_eval_md=False)
    assert "f01" in reports
    assert "f10" not in reports
    assert "f11" not in reports


# ── 6. calib_metric_status 계약 및 표본 0 검증 ─────────────────────


def test_calib_metric_status_handles_all_edge_cases():
    """calib_metric_status의 완전한 계약 검증."""
    # 1. 기존 호환 (인자 4개)
    tpr, tnr, st = L.calib_metric_status(0, 0, 0, 0)
    assert st == "⚠️ 표본 0 (미검증)"
    tpr, tnr, st = L.calib_metric_status(0, 0, 5, 0)
    assert st == "⚠️ FAIL 표본 0 (재현력 미검증)"
    tpr, tnr, st = L.calib_metric_status(5, 0, 0, 0)
    assert st == "⚠️ PASS 표본 0 (오탐 미검증)"
    tpr, tnr, st = L.calib_metric_status(9, 1, 9, 1)
    assert st == "✅ 통과"
    tpr, tnr, st = L.calib_metric_status(7, 3, 9, 1)
    assert st == "⚠️ 보정 필요"

    # 2. 격리 (is_quarantine)
    tpr, tnr, st = L.calib_metric_status(10, 0, 10, 0, is_quarantine=True)
    assert st == "⚠️ QUARANTINE_LABEL_CONFLICT"

    # 3. provenance 부재
    tpr, tnr, st = L.calib_metric_status(10, 0, 10, 0, has_provenance=False)
    assert "역사적 관측치" in st

    # 4. provenance 불일치
    tpr, tnr, st = L.calib_metric_status(10, 0, 10, 0, provenance_match=False)
    assert "provenance 불일치" in st

    # 5. 미채점 건수 존재
    tpr, tnr, st = L.calib_metric_status(
        10, 0, 10, 0, unvalidated_count=2, unvalidated_reasons=["기계 ERROR 1건", "입출력 잘림 1건"]
    )
    assert "UNKNOWN/NOT_VALIDATED" in st
    assert "기계 ERROR 1건" in st
    assert "입출력 잘림 1건" in st
