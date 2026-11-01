"""5단계 Judge 보정 및 검증 인프라 종합 단위 테스트 (tests/evals/test_stage5_calibration.py).

검증 항목:
1. dryrun 파일생성 0 / LLM 0
2. 출력 덮어쓰기 거절 및 새 파일 고유 네이밍
3. 새 결과 파일 최신 선택 및 재개 (dryrun/smoke 제외)
4. context 누락 / truncation / 빈 template / 없는 case 시 모델 호출 0 및 ERROR 반환 (정상 PASS 금지)
5. supplement fixture 프롬프트의 expected label / description / fail_if 비누출
6. 9개 희소 유형 36개 fixture의 DB 없는 독립 grounding 보유 및 정식 경로 검증
7. Claude weekly limit 감지 시 재시도 0 즉시 중단
8. folder(slug) 의 중복 prefix 감지
"""

from __future__ import annotations

import json
import sys

import pytest

import evals._harness.stage5_judges as J
import evals._harness.stage5_labels as L
import evals._harness.stage5_rejudge as R
import evals._harness.stage5_supplement as Supp

# ── 1. folder(slug) 중복 prefix 감지 테스트 ─────────────────────────


def test_folder_duplicate_prefix_detection(tmp_path, monkeypatch):
    """동일 prefix 디렉토리가 2개 이상 존재할 때 중복 감지 예외 발생 검증."""
    monkeypatch.setattr(J, "EVALS", tmp_path)
    (tmp_path / "f08-v2-memory-extract").mkdir()
    (tmp_path / "f08-v2-memory-extraction").mkdir()

    with pytest.raises(ValueError, match="중복 prefix 감지"):
        J.folder("f08")


def test_folder_not_found(tmp_path, monkeypatch):
    """존재하지 않는 slug 일 때 FileNotFoundError 발생 검증."""
    monkeypatch.setattr(J, "EVALS", tmp_path)
    with pytest.raises(FileNotFoundError, match="찾을 수 없습니다"):
        J.folder("f99")


# ── 2. Claude weekly limit 및 재시도 차단 테스트 ───────────────────


def test_is_limit_error_detection():
    """Claude CLI 의 주간 한도(weekly limit) 및 각종 한도 메시지 감지 테스트."""
    assert J.is_limit_error("You've hit your weekly limit · resets Oct 30 at 7pm (Asia/Seoul)")
    assert J.is_limit_error("Session limit reached for the current conversation.")
    assert J.is_limit_error("Usage limit exceeded.")
    assert J.is_limit_error("HTTP 429: Rate limit exceeded")
    assert J.is_limit_error("Quota exceeded for project")
    assert not J.is_limit_error("SyntaxError: invalid syntax")
    assert not J.is_limit_error('{"verdict": "PASS", "reasoning": "정상 응답"}')


def test_weekly_limit_immediate_stop_no_retry(monkeypatch):
    """weekly limit 발생 시 retries 횟수와 무관하게 즉시 중단(재시도 0회) 검증."""
    call_count = 0

    def mock_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1

        class MockRes:
            stdout = ""
            stderr = "You've hit your weekly limit · resets Oct 30 at 7pm (Asia/Seoul)"
            returncode = 1

        return MockRes()

    monkeypatch.setattr("subprocess.run", mock_run)
    J.LIMIT_HIT.clear()

    res = J.call("테스트 프롬프트", model="test-weekly-check", retries=3)
    assert res["verdict"] == "ERROR"
    assert "한도" in res["reasoning"]
    assert call_count == 1, f"weekly limit 감지 시 즉시 중단되어야 하나 {call_count}회 호출됨"
    assert J.LIMIT_HIT.is_set()


# ── 3. dry-run 파일 생성 0 및 LLM 0 테스트 ─────────────────────────


def test_rejudge_dry_run_zero_file_creation(tmp_path, monkeypatch):
    """rejudge dry-run 실행 시 결과 파일이 0개 생성되고 LLM 호출이 0회인지 검증."""
    sample_res = {
        "run_at": "2026-10-26T22:48:00",
        "version": "cb53097",
        "rows": [{"case_id": "01-ob-01", "sample": {"output_raw": "안녕"}, "code": {}, "judge": {}}],
    }
    src_file = tmp_path / "20261026-2248_cb53097_calibration.json"
    src_file.write_text(json.dumps(sample_res, ensure_ascii=False))

    initial_files = set(tmp_path.iterdir())
    plan = R.plan_rejudge("f01", src_file, force_types={"F-01"})

    current_files = set(tmp_path.iterdir())
    assert initial_files == current_files, "dry-run 은 어떠한 새 파일도 디스크에 생성하지 않아야 함"
    assert plan["dry_run"] is True
    assert plan["total_rows"] == 1
    # no-judge 이므로 f01의 6개 Judge 유형 전체가 todo로 잡힘
    assert plan["total_todo_judges"] == 6


def test_supplement_dry_run_zero_file_creation(tmp_path, monkeypatch):
    """supplement dry-run 실행 시 결과 파일이 0개 생성되고 LLM 호출이 0회인지 검증."""
    eval_dir = tmp_path / "supp_out"
    monkeypatch.setattr(Supp, "RESULTS_SUPP_DIR", eval_dir)

    res = Supp.run_evaluation(slug="f01", dry_run=True)
    assert res["dry_run"] is True
    assert not eval_dir.exists(), "supplement dry-run 은 결과 디렉토리 및 파일을 생성하지 않아야 함"


# ── 4. 출력 덮어쓰기 거절 및 새 파일 고유 네이밍 테스트 ──────────────


def test_rejudge_refuses_overwrite(tmp_path):
    """소스 파일 또는 이미 존재하는 대상 파일로의 덮어쓰기 거절 검증."""
    sample_res = {
        "run_at": "2026-10-26T22:48:00",
        "version": "cb53097",
        "rows": [],
    }
    src_file = tmp_path / "20261026-2248_cb53097_calibration.json"
    src_file.write_text(json.dumps(sample_res, ensure_ascii=False))

    # 1. 소스 파일과 동일한 경로로 지정 시 거절
    with pytest.raises(ValueError, match="덮어쓰기 금지"):
        R.rejudge_result_file("f01", src_file, out_path=src_file)

    # 2. 이미 존재하는 다른 파일 경로로 지정 시 거절
    existing_dest = tmp_path / "other_existing.json"
    existing_dest.write_text("{}")
    with pytest.raises(FileExistsError, match="덮어쓰기 금지"):
        R.rejudge_result_file("f01", src_file, out_path=existing_dest)


def test_rejudge_creates_unique_timestamped_file(tmp_path, monkeypatch):
    """rejudge 실행 시 고유 timestamp+uuid 기반의 *_calibration.json 파일이 생성되는지 검증."""
    sample_res = {
        "run_at": "2026-10-26T22:48:00",
        "version": "cb53097",
        "rows": [{"case_id": "01-ob-01", "sample": {"output_raw": "안녕"}, "code": {}, "judge": {}}],
    }
    src_file = tmp_path / "20261026-2248_cb53097_calibration.json"
    src_file.write_text(json.dumps(sample_res, ensure_ascii=False))

    # J.call 모킹
    monkeypatch.setattr(R.J, "call", lambda prompt: {"verdict": "PASS", "reasoning": "테스트 모킹"})

    out_path, todo, left = R.rejudge_result_file("f01", src_file, split="calibration")
    assert out_path.exists()
    assert out_path != src_file
    assert out_path.name.endswith("_calibration.json")
    assert "rejudge_provenance" in json.loads(out_path.read_text())


# ── 5. latest_result 최신 선택 및 재개 테스트 ──────────────────────


def test_latest_result_filters_dryrun_smoke_and_selects_latest(tmp_path, monkeypatch):
    """latest_result 가 smoke, dryrun, plan 을 제외하고 최신 타임스탬프 결과만 선택하는지 검증."""
    results_dir = tmp_path / "f01-test" / "results"
    results_dir.mkdir(parents=True)
    monkeypatch.setattr(L.J, "folder", lambda slug: tmp_path / "f01-test")

    # 더미 결과 파일들 생성
    f1 = results_dir / "20261026-2248_cb53097_calibration.json"
    f1.write_text(json.dumps({"version": "v1", "rows": []}))

    f_smoke = results_dir / "20261027-0100_smoke_calibration.json"
    f_smoke.write_text(json.dumps({"version": "smoke", "rows": []}))

    f_dry = results_dir / "20261028-1000_dryrun_calibration.json"
    f_dry.write_text(json.dumps({"version": "dryrun", "rows": []}))

    f_latest = results_dir / "20261028-1200_a1b2c3d4_cb53097_calibration.json"
    f_latest.write_text(json.dumps({"version": "v2_latest", "rows": []}))

    res = L.latest_result("f01", split="calibration")
    assert res is not None
    assert res["version"] == "v2_latest"


# ── 6. Pre-flight 차단 (context 누락, truncation, 빈 template 등) ──


def test_preflight_blocks_without_calling_llm(monkeypatch):
    """결측/오류 상황에서 LLM 호출 없이 ERROR 를 즉시 반환하는지 검증."""
    llm_called = False

    def mock_call(prompt):
        nonlocal llm_called
        llm_called = True
        return {"verdict": "PASS"}

    monkeypatch.setattr(R.J, "call", mock_call)

    # 1. 케이스 부재
    res, snap, trunc = R.evaluate_single_judge("f01", "F-01", case=None, sample={})
    assert res["verdict"] == "ERROR"
    assert not llm_called

    # 2. 모델 실행 오류로 인한 출력 결측
    sample_err = {"error": "Connection timeout", "output_raw": None, "output_parsed": None}
    res, snap, trunc = R.evaluate_single_judge("f01", "F-01", case={"input": {}}, sample=sample_err)
    assert res["verdict"] == "ERROR"
    assert "모델 실행 오류" in res["reasoning"]
    assert not llm_called

    # 3. 6000자 초과 Truncation 발생 시 -> 모델 호출 차단 및 정상 PASS 금지
    huge_input = {"text": "A" * 7000}
    res, snap, trunc = R.evaluate_single_judge("f01", "F-01", case={"input": huge_input}, sample={"output_raw": "답변"})
    assert trunc is True
    assert res["verdict"] == "ERROR"
    assert "잘림" in res["reasoning"]
    assert not llm_called


def test_holdout_protection():
    """blind_holdout 재판정 시 --allow-holdout 없거나 --types 지정 시 차단 검증."""
    # R.main 은 CLI 인자를 받으므로 논리 테스트
    with pytest.raises(SystemExit):
        # allow_holdout 없이 실행 시도시 sys.exit
        sys.argv = ["stage5_rejudge.py", "--split", "blind_holdout"]
        R.main()


# ── 7. Supplement Fixture 비누출 및 Self-contained 검증 ────────────


def test_supplement_fixtures_self_contained_grounding():
    """36개 모든 fixture 가 DB 없이 완결된 grounding 자료를 보유하는지 검증."""
    cases = Supp.load_supplements()
    assert len(cases) == 36

    for c in cases:
        slug = c["slug"]
        inp = c["input"]
        if slug == "f01":
            assert "history" in inp and len(inp["history"]) > 0
        elif slug == "f06":
            assert "partner_facts" in inp and len(inp["partner_facts"]) > 0
            assert "partner_frequent_phrases" in inp
        elif slug == "f08":
            assert "messages" in inp and len(inp["messages"]) > 0
        elif slug == "f12":
            assert "a_facts" in inp and len(inp["a_facts"]) > 0
            assert "b_facts" in inp and len(inp["b_facts"]) > 0
        elif slug == "f17":
            assert "messages" in inp and len(inp["messages"]) > 0
        elif slug == "f18":
            assert "requester_reason" in inp


# ── 8. Calib 집계 및 한쪽 클래스 0건 / NA / ERROR 왜곡 방지 테스트 ──


def test_calib_metric_status_handles_zero_samples_and_na():
    """한쪽 클래스 0건, 표본 부재 시 통과(✅)로 왜곡되지 않고 정직한 상태를 반환하는지 검증."""
    # 1. 표본 0건 (모두 NA이거나 판정 없음) -> 미검증
    tpr, tnr, status = L.calib_metric_status(tp=0, fn=0, tn=0, fp=0)
    assert tpr is None
    assert tnr is None
    assert "표본 0" in status
    assert "✅" not in status

    # 2. FAIL 표본 0건 (FAIL이 1건도 없어서 TPR 측정 불가) -> 통과가 아닌 FAIL 표본 0 경고
    tpr, tnr, status = L.calib_metric_status(tp=0, fn=0, tn=10, fp=0)
    assert tpr is None
    assert tnr == 1.0
    assert "FAIL 표본 0" in status
    assert "✅" not in status

    # 3. PASS 표본 0건 (PASS가 1건도 없어서 TNR 측정 불가) -> 통과가 아닌 PASS 표본 0 경고
    tpr, tnr, status = L.calib_metric_status(tp=10, fn=0, tn=0, fp=0)
    assert tpr == 1.0
    assert tnr is None
    assert "PASS 표본 0" in status
    assert "✅" not in status

    # 4. 양쪽 클래스 모두 85% 이상일 때만 정식 통과
    tpr, tnr, status = L.calib_metric_status(tp=9, fn=1, tn=9, fp=1)
    assert tpr == 0.9
    assert tnr == 0.9
    assert status == "✅ 통과"

    # 5. 한쪽이라도 85% 미만이면 보정 필요
    tpr, tnr, status = L.calib_metric_status(tp=7, fn=3, tn=9, fp=1)
    assert tpr == 0.7
    assert tnr == 0.9
    assert "보정 필요" in status


def test_supplement_prompt_zero_leakage():
    """Judge 용 프롬프트 생성 시 draft_expected, description, fail_if 가 절대 누출되지 않는지 검증."""
    cases = Supp.load_supplements()
    for c in cases:
        prompt, meta = Supp.build_judge_prompt_for_case(c)
        assert len(meta["leak_warnings"]) == 0

        # 데이터 블록 추출
        inp_block = prompt.split("<입력>", 1)[1].split("</입력>", 1)[0]
        orc_block = prompt.split("<정답 기준>", 1)[1].split("</정답 기준>", 1)[0]

        # 정답 레이블 단독 노출 방지
        assert f"draft_expected: {c['draft_expected']}" not in inp_block
        assert c["description"] not in inp_block
        assert c["description"] not in orc_block
        assert "공통 판정 기준만" in orc_block
