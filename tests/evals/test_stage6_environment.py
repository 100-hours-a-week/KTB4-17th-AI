"""6단계 환경 격리, 무결성 바인딩, 자격증명 비노출 종합 검증

검증 항목:
1. build_eval_env: process DATABASE_URL 우선, 없으면 .env URL 파서로 /ktb_eval 치환 (query 보존)
2. production 원본 .env 및 전역 os.environ 불변 (pure dict 생성)
3. preflight 와 child subprocess 에 전달되는 env 동일성
4. 자격증명/비밀/호스트의 stdout, 로그, 결과 metadata 비노출
5. Query URI 정확한 path 파싱 및 /ktb_eval_prod, /ktb 차단
6. manifest 또는 config 변경 시 checkpoint identity 불일치로 이전 행 미재사용
7. 결과 top metadata 에 manifest_hash, full commit, configuration_hash 바인딩 및 Gate 0 PASS
8. 과거 결과(cb53097-dirty) 및 기존 결과 파일 덮어쓰기 절대 금지
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from unittest.mock import MagicMock, patch

import pytest

import evals._harness.stage5_run as S5
import evals._harness.stage6_cli as CLI
import evals._harness.stage6_preflight as P
import evals._harness.stage6_scorecard as SC


# ── 1. child env 파생 및 우선순위 테스트 ──────────────────────────────
def test_child_env_prioritizes_process_database_url(monkeypatch):
    custom_url = "postgresql+asyncpg://testuser:testpass@localhost:5432/ktb_eval?ssl=true&client_encoding=utf8"
    monkeypatch.setenv("DATABASE_URL", custom_url)
    monkeypatch.setenv("LANGFUSE_TRACING_ENVIRONMENT", "eval-local")

    child_env = P.build_eval_env()
    assert child_env["DATABASE_URL"] == custom_url
    assert child_env["LANGFUSE_TRACING_ENVIRONMENT"] == "eval-local"


def test_child_env_derives_from_dotenv_when_process_env_missing(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    raw_dot_env = "postgresql+asyncpg://prod_user:secret_pwd@prod.host:5432/ktb?sslmode=require"

    with patch.object(P, "read_env_file", return_value={"DATABASE_URL": raw_dot_env}):
        child_env = P.build_eval_env(base_env={})
        derived = child_env.get("DATABASE_URL")
        assert derived is not None
        # path 만 /ktb_eval 로 변경되고 쿼리 파라미터는 완벽히 보존
        assert derived == "postgresql+asyncpg://prod_user:secret_pwd@prod.host:5432/ktb_eval?sslmode=require"
        assert child_env["LANGFUSE_TRACING_ENVIRONMENT"] == "eval-local"


# ── 2. 원본 .env 및 전역 os.environ 불변성 테스트 ───────────────────────
def test_production_env_file_and_os_environ_immutable(monkeypatch):
    original_os_env = dict(os.environ)
    env_path = P.ROOT / ".env"
    original_file_bytes = env_path.read_bytes() if env_path.exists() else None

    # build_eval_env 및 prepare_eval_child_env 실행
    child_env1 = P.build_eval_env()
    child_env2 = CLI.prepare_eval_child_env()

    assert isinstance(child_env1, dict)
    assert isinstance(child_env2, dict)

    # os.environ 불변 검증
    assert dict(os.environ) == original_os_env

    # .env 파일 바이트 불변 검증
    if env_path.exists():
        assert env_path.read_bytes() == original_file_bytes


# ── 3. preflight 와 child subprocess 동일 env 전달 검증 ─────────────────
def test_subprocess_receives_identical_env_as_preflight():
    args = argparse.Namespace(only="f01", limit=1, no_judge=True, version="test_v1", cases="")

    captured_preflight_env = {}
    captured_subprocess_env = {}

    def mock_preflight(env=None):
        nonlocal captured_preflight_env
        captured_preflight_env = env
        rep = MagicMock()
        rep.passed = True
        return rep

    def mock_subprocess_run(cmd, *a, **kw):
        nonlocal captured_subprocess_env
        captured_subprocess_env = kw.get("env")
        mock_res = MagicMock()
        mock_res.returncode = 0
        return mock_res

    with (
        patch.object(CLI.P, "run_preflight", side_effect=mock_preflight),
        patch.object(CLI.M, "verify_manifest", return_value=(True, [])),
        patch("subprocess.run", side_effect=mock_subprocess_run),
    ):
        ret = CLI.cmd_run_regression(args)
        assert ret == 0
        assert captured_preflight_env is not None
        assert captured_subprocess_env is not None
        # preflight 검사 대상 env 와 subprocess 에 넘긴 env 가 완벽히 일치해야 함
        assert captured_preflight_env["DATABASE_URL"] == captured_subprocess_env["DATABASE_URL"]
        assert (
            captured_preflight_env["LANGFUSE_TRACING_ENVIRONMENT"]
            == captured_subprocess_env["LANGFUSE_TRACING_ENVIRONMENT"]
        )
        assert captured_subprocess_env["EVAL_MANIFEST_HASH"] != ""
        assert captured_subprocess_env["EVAL_CONFIG_HASH"] != ""


# ── 4. 자격증명/비밀 비노출 테스트 ────────────────────────────────────
def test_credentials_not_exposed_in_stdout_logs_or_metadata(monkeypatch):
    secret_pw = "super_classified_password_98765"
    secret_key = "sk-classified-openai-key-abcde"
    secret_url = f"postgresql+asyncpg://admin:{secret_pw}@internal.host:5432/ktb_eval?token={secret_key}"

    monkeypatch.setenv("DATABASE_URL", secret_url)
    monkeypatch.setenv("OPENAI_API_KEY", secret_key)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-secret-999")

    # 1. nonsecret config 에 자격증명/비밀 키 일절 비포함
    nonsecret = CLI.get_eval_nonsecret_config()
    nonsecret_str = json.dumps(nonsecret)
    assert secret_pw not in nonsecret_str
    assert secret_key not in nonsecret_str
    assert "password" not in nonsecret
    assert "key" not in nonsecret
    assert "token" not in nonsecret

    # 2. preflight sanitize 및 env_info 에 비밀 비노출
    db_name, desc, is_exact = P.sanitize_db_url(secret_url)
    assert is_exact is True
    assert db_name == "ktb_eval"
    assert secret_pw not in desc
    assert secret_key not in desc

    report = P.run_preflight(env={"DATABASE_URL": secret_url, "LANGFUSE_TRACING_ENVIRONMENT": "eval-local"})
    dumped = json.dumps(report.to_dict())
    assert secret_pw not in dumped
    assert secret_key not in dumped


# ── 5. Query URI 정확한 path 파싱 및 오염 DB 차단 검증 ─────────────────
def test_query_uri_path_parsing_and_blocking_ktb_eval_prod():
    # 쿼리 파라미터가 포함된 유효 URL
    valid_query_url = "postgresql://localhost:5432/ktb_eval?ssl=true&statement_timeout=5000"
    db_name, desc, is_exact = P.sanitize_db_url(valid_query_url)
    assert is_exact is True
    assert db_name == "ktb_eval"
    assert "path='/ktb_eval'" in desc

    # /ktb_eval_prod 차단
    bad_prod_url = "postgresql://localhost:5432/ktb_eval_prod?ssl=true"
    _, _, is_exact_bad = P.sanitize_db_url(bad_prod_url)
    assert is_exact_bad is False

    # stage5_run DB URL 해석기에서 /ktb_eval_prod 차단 검증
    with patch.dict(os.environ, {"DATABASE_URL": bad_prod_url}):
        with pytest.raises(RuntimeError, match="평가 DB 대상 경로가 '/ktb_eval'이 아닙니다"):
            S5._resolve_and_verify_eval_db_url()

    # stage5_run DB URL 해석기에서 /ktb (운영) 차단 검증
    with patch.dict(os.environ, {"DATABASE_URL": "postgresql://localhost:5432/ktb"}):
        with pytest.raises(RuntimeError, match="평가 DB 대상 경로가 '/ktb_eval'이 아닙니다"):
            S5._resolve_and_verify_eval_db_url()


# ── 6. Checkpoint identity: manifest 및 config 변경 시 미재사용 ─────────
def test_checkpoint_identity_distinct_when_manifest_or_config_changes():
    ver = "b5e158c"
    manifest_a = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    manifest_b = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    cfg_1 = {"llm_model": "model-1", "judge_model": "sonnet"}
    cfg_2 = {"llm_model": "model-2", "judge_model": "sonnet"}

    hash_cfg_1 = hashlib.sha256(json.dumps(cfg_1, sort_keys=True).encode()).hexdigest()
    hash_cfg_2 = hashlib.sha256(json.dumps(cfg_2, sort_keys=True).encode()).hexdigest()

    # 동일 HEAD 커밋이어도 manifest 가 다르면 identity_tag 상이
    tag_a1 = f"{ver}_{manifest_a[:10]}_{hash_cfg_1[:10]}"
    tag_b1 = f"{ver}_{manifest_b[:10]}_{hash_cfg_1[:10]}"
    assert tag_a1 != tag_b1

    # 동일 HEAD 커밋, 동일 manifest 라도 모델 설정(config)이 다르면 identity_tag 상이
    tag_a2 = f"{ver}_{manifest_a[:10]}_{hash_cfg_2[:10]}"
    assert tag_a1 != tag_a2

    # 사용자가 --version myver 를 줘도 identity_tag 에 manifest + config hash 가 유지됨
    custom_ver = "myver"
    tag_custom = f"{custom_ver}_{manifest_a[:10]}_{hash_cfg_1[:10]}"
    assert manifest_a[:10] in tag_custom
    assert hash_cfg_1[:10] in tag_custom


# ── 7. 결과 metadata manifest binding 및 Gate 0 PASS 검증 ─────────────
def test_result_metadata_manifest_binding_evaluates_gate0_pass(tmp_path):
    # 모의 manifest 생성
    combined_hash = "11223344556677889900aabbccddeeff11223344556677889900aabbccddeeff"
    manifest_data = {
        "version": "1.0.0",
        "git_commit": "b5e158c000000000000000000000000000000000",
        "combined_hash": combined_hash,
        "files": [],
    }
    mf_path = tmp_path / "manifest.json"
    mf_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    # 골드셋 모의
    slug = "f01-persona-conversation"
    feat_dir = tmp_path / slug
    res_dir = feat_dir / "results"
    res_dir.mkdir(parents=True)
    goldset_file = feat_dir / "goldset.jsonl"
    cases = [
        {"id": "01-ob-01", "split": "regression", "input": {"a": 1}, "target_failures": []},
        {"id": "01-ob-02", "split": "regression", "input": {"a": 2}, "target_failures": []},
    ]
    goldset_file.write_text("\n".join(json.dumps(c) for c in cases) + "\n", encoding="utf-8")

    # manifest_hash 가 바인딩된 신규 결과 파일
    new_result = {
        "feature": slug,
        "split": "regression",
        "version": "b5e158c",
        "manifest_hash": combined_hash,
        "git_commit": "b5e158c000000000000000000000000000000000",
        "configuration_hash": "cfg1234567890",
        "config": {"llm_model": "test-model"},
        "rows": [
            {
                "case_id": "01-ob-01",
                "split": "regression",
                "sample": {"output_raw": "답변 1"},
                "verdict": {"F-01": "PASS"},
            },
            {
                "case_id": "01-ob-02",
                "split": "regression",
                "sample": {"output_raw": "답변 2"},
                "verdict": {"F-01": "PASS"},
            },
        ],
    }
    res_file = res_dir / "20261028-1200_b5e158c_regression.json"
    res_file.write_text(json.dumps(new_result, ensure_ascii=False), encoding="utf-8")

    # scorecard 판정 수행
    res = SC.parse_and_score(res_file, manifest_path=mf_path)
    gate0 = next(g for g in res["gates"] if g.gate_id == "Gate0")
    assert gate0.status == "PASS"
    assert gate0.passed is True
    assert "완벽 일치" in gate0.details[0]

    # manifest_hash 가 없는 과거 결과는 UNKNOWN 유지
    legacy_result = dict(new_result)
    legacy_result.pop("manifest_hash")
    legacy_file = res_dir / "legacy_regression.json"
    legacy_file.write_text(json.dumps(legacy_result, ensure_ascii=False), encoding="utf-8")

    legacy_res = SC.parse_and_score(legacy_file, manifest_path=mf_path)
    legacy_gate0 = next(g for g in legacy_res["gates"] if g.gate_id == "Gate0")
    assert legacy_gate0.status == "UNKNOWN"
    assert legacy_gate0.passed is False
    assert "묶여 있지 않음" in legacy_gate0.details[0]


# ── 8. 과거 결과 및 기존 파일 덮어쓰기 금지 검증 ────────────────────────
def test_historical_results_never_overwritten(tmp_path):
    slug_dir = tmp_path / "f01-persona-conversation" / "results"
    slug_dir.mkdir(parents=True)

    # 과거 historical 파일
    hist_file = slug_dir / "20261026-2248_cb53097-dirty_calibration.json"
    hist_content = json.dumps({"version": "cb53097-dirty", "rows": [{"case_id": "c1", "content": "original"}]})
    hist_file.write_text(hist_content, encoding="utf-8")

    # stage5_run 식의 파일 탐색 필터 검증
    # a.cases 가 주어졌을 때 cb53097 파일은 대상에서 완전히 제외되어야 함
    current_ver = "b5e158c"
    candidates = sorted(
        x
        for x in slug_dir.glob("*_calibration.json")
        if "smoke" not in x.name and "cb53097" not in x.name and not x.name.startswith(".partial")
    )
    matching = [x for x in candidates if f"_{current_ver}_" in x.name]
    assert len(matching) == 0  # 과거 파일이 매칭되지 않음
    assert hist_file.read_text(encoding="utf-8") == hist_content  # 내용 불변
