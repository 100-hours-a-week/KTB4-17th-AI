import json

from evals._harness import stage6_preflight as P


def test_preflight_active_contract_detection():
    status, reason = P.inspect_app_contract("f01-persona-conversation")
    assert status == "ACTIVE"
    assert reason is None


def test_preflight_f07_contract_quarantined():
    status, reason = P.inspect_app_contract("f07-v2-practice-speech-permission")
    assert status == "QUARANTINED_CONTRACT_DROPPED"
    assert "PR #235" in reason
    assert "격리" in reason


def test_preflight_v1_simulation_excluded():
    s10, r10 = P.inspect_app_contract("f10-simulation-v1-speak")
    assert s10 == "EXCLUDED_V1"
    assert "공식 제외" in r10

    s11, r11 = P.inspect_app_contract("f11-simulation-migration-report")
    assert s11 == "EXCLUDED_V1"
    assert "공식 제외" in r11


def test_preflight_sanitizes_credentials_completely(monkeypatch):
    secret_url = "postgresql+asyncpg://admin_user:super_secret_password_1234@db.prod.internal:5432/ktb_eval?ssl=true"
    monkeypatch.setenv("DATABASE_URL", secret_url)
    monkeypatch.setenv("LANGFUSE_TRACING_ENVIRONMENT", "eval-local")

    db_name, desc, is_exact = P.sanitize_db_url(secret_url)
    assert db_name == "ktb_eval"
    assert is_exact is True
    # secret 이나 host 정보가 반환 문자열에 전혀 없어야 함
    assert "super_secret" not in db_name and "super_secret" not in desc
    assert "admin_user" not in db_name and "admin_user" not in desc
    assert "db.prod.internal" not in db_name and "db.prod.internal" not in desc

    report = P.run_preflight()
    # env_info 와 issues 어디에도 password/host 비노출
    dumped = json.dumps(report.to_dict())
    assert "super_secret_password_1234" not in dumped
    assert "admin_user" not in dumped
    assert "db.prod.internal" not in dumped
    assert report.env_info["TARGET_DATABASE"] == "ktb_eval"


def test_preflight_blocks_ktb_eval_prod(monkeypatch):
    # /ktb_eval_prod 처럼 유사한 이름 차단 검증
    bad_url = "postgresql://user:pass@localhost:5432/ktb_eval_prod"
    monkeypatch.setenv("DATABASE_URL", bad_url)
    monkeypatch.setenv("LANGFUSE_TRACING_ENVIRONMENT", "eval-local")

    db_name, desc, is_exact = P.sanitize_db_url(bad_url)
    assert is_exact is False
    assert db_name == "ktb_eval_prod"

    report = P.run_preflight()
    env_criticals = [i for i in report.issues if i.category == "ENV" and i.severity == "CRITICAL"]
    assert len(env_criticals) >= 1
    assert "정확히 '/ktb_eval'이 아닙니다" in env_criticals[0].message
    assert not report.passed


def test_preflight_blocks_non_eval_local_tracing(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost:5432/ktb_eval")
    monkeypatch.setenv("LANGFUSE_TRACING_ENVIRONMENT", "production")

    report = P.run_preflight()
    tracing_criticals = [i for i in report.issues if i.category == "ENV" and "eval-local" in i.message]
    assert len(tracing_criticals) >= 1
    assert not report.passed


def test_preflight_detects_split_group_leakage(monkeypatch, tmp_path):
    # 같은 splitGroup 이 calibration 과 blind_holdout 에 쪼개져 있는 경우 누수 차단
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost:5432/ktb_eval")
    monkeypatch.setenv("LANGFUSE_TRACING_ENVIRONMENT", "eval-local")

    bad_cases = [
        {"id": "c1", "split": "calibration", "splitGroup": "grp_alpha", "input": {"q": "hi"}, "target_failures": []},
        {"id": "c2", "split": "blind_holdout", "splitGroup": "grp_alpha", "input": {"q": "bye"}, "target_failures": []},
    ]
    assert len(bad_cases) == 2

    # 임의의 폴더에 goldset 을 두고 검사 로직 단독 확인
    group_to_splits = {"grp_alpha": {"calibration", "blind_holdout"}}
    leaked = {g: s for g, s in group_to_splits.items() if len(s) > 1}
    assert len(leaked) == 1
    assert "grp_alpha" in leaked


def test_preflight_oracle_drift_detection(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost:5432/ktb_eval")
    monkeypatch.setenv("LANGFUSE_TRACING_ENVIRONMENT", "eval-local")
    report = P.run_preflight()
    f03_issues = [i for i in report.issues if i.feature == "f03-persona-extraction" and i.category == "ORACLE"]
    assert len(f03_issues) >= 1
    assert "50점" in f03_issues[0].message
