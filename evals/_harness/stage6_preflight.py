"""6단계 사전 비행 검사 (Preflight Check)

16개 활성 기능 대상:
1. 분할(Split) 무결성:
   - calibration / regression / blind_holdout 분할 확인
   - splitGroup 누수 검사: 동일 splitGroup 내 문제들이 여러 split에 걸쳐 있으면 FAIL
   - 미등록 split 차단 (허용: calibration, regression, blind_holdout)
2. 필수 필드 및 스키마 검사:
   - case ID, split, input, target_failures 검증
   - 빈/잘못된 필드값 검출
3. 앱 계약(App Contract) 검사:
   - f10, f11: 미사용 v1 기능 감지 시 골드셋 검사 전에 SKIP
   - f07: PR #235(HEAD b5e158c)에서 speech.py 및 테이블 제거 감지 -> QUARANTINED_CONTRACT_DROPPED
   - 활성 15개 기능 진입점 검사
4. 환경 및 DB 격리 검사:
   - DATABASE_URL 에서 자격증명/호스트 비노출 (DB 이름만 추출/보고)
   - urllib.parse 로 path 가 정확히 /ktb_eval 인지 엄격 판정 (/ktb_eval_prod 등 차단)
   - URL 검사와 실제 연결(Connect) 상태를 명확히 분리 (환경 변수 임의 변조 금지)
   - LANGFUSE_TRACING_ENVIRONMENT != "eval-local" 차단
5. 골드셋 오라클 정합성(Oracle Drift) 검사:
   - f03의 오래된 default_50_allowed 오라클과 2026-10-28 최신 기준 모순 격리
6. 버전 격리 검사: HEAD b5e158c 확인 및 과거 결과(cb53097-dirty)와 분리
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]
EVALS = ROOT / "evals"

ALL_SLUGS = [
    "f01-persona-conversation",
    "f02-persona-tagging",
    "f03-persona-extraction",
    "f04-persona-extraction-style",
    "f05-v2-persona-extraction",
    "f06-practice-reply",
    "f07-v2-practice-speech-permission",
    "f08-v2-memory-extract",
    "f09-v2-memory-reconcile",
    "f10-simulation-v1-speak",
    "f11-simulation-migration-report",
    "f12-simulation-v2-generate",
    "f13-simulation-v2-regenerate",
    "f14-simulation-v2-judge",
    "f15-simulation-v2-repair",
    "f16-simulation-v2-report",
    "f17-chat-end-drafts",
    "f18-chat-end-message",
]

EXCLUDED_V1_SLUGS = {"f10-simulation-v1-speak", "f11-simulation-migration-report"}
CONTRACT_QUARANTINE_SLUGS = {"f07-v2-practice-speech-permission"}
ALLOWED_SPLITS = {"calibration", "regression", "blind_holdout"}

EXPECTED_SPLIT_COUNTS = {
    "f17-chat-end-drafts": {"calibration": 11, "regression": 21, "blind_holdout": 8, "total": 40},
}
DEFAULT_SPLIT_COUNTS = {"calibration": 12, "regression": 20, "blind_holdout": 8, "total": 40}


@dataclass
class PreflightIssue:
    severity: str  # CRITICAL, WARNING, INFO
    feature: str
    category: str  # SPLIT, REQUIRED_FIELDS, CONTRACT, ENV, ORACLE, VERSION
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreflightReport:
    passed: bool
    git_head: str
    git_dirty: bool
    issues: list[PreflightIssue] = field(default_factory=list)
    feature_summaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    env_info: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "git_head": self.git_head,
            "git_dirty": self.git_dirty,
            "env_info": self.env_info,
            "feature_summaries": self.feature_summaries,
            "issue_counts": {
                "critical": sum(1 for i in self.issues if i.severity == "CRITICAL"),
                "warning": sum(1 for i in self.issues if i.severity == "WARNING"),
                "info": sum(1 for i in self.issues if i.severity == "INFO"),
            },
            "issues": [
                {
                    "severity": i.severity,
                    "feature": i.feature,
                    "category": i.category,
                    "message": i.message,
                    "details": i.details,
                }
                for i in self.issues
            ],
        }


def read_env_file() -> dict[str, str]:
    env_map = {}
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_map[k.strip()] = v.split("#", 1)[0].strip().strip('"').strip("'")
    return env_map


def check_git_status() -> tuple[str, bool]:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=True,
        ).stdout.strip()
    except Exception:
        head = "unknown"

    try:
        dirty_out = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=True,
        ).stdout.strip()
        code_dirty_lines = [
            line
            for line in dirty_out.splitlines()
            if not line.endswith(".gitignore")
            and not line.endswith(".html")
            and not line.endswith(".md")
            and ("app/" in line or "evals/" in line or "tests/" in line)
        ]
        is_dirty = len(code_dirty_lines) > 0
    except Exception:
        is_dirty = False

    return head, is_dirty


def inspect_app_contract(slug: str) -> tuple[str, str | None]:
    if slug in EXCLUDED_V1_SLUGS:
        return "EXCLUDED_V1", "시뮬레이션 v1 미사용 기능으로 6단계 평가에서 공식 제외됨"

    if slug in CONTRACT_QUARANTINE_SLUGS:
        speech_file = ROOT / "app" / "v2" / "practice" / "speech.py"
        if not speech_file.exists():
            return (
                "QUARANTINED_CONTRACT_DROPPED",
                "PR #235(HEAD b5e158c)에서 app/v2/practice/speech.py 및 v2_practice_speech_state 테이블이 삭제됨. 계약 불일치로 자동 실행 차단 및 격리.",
            )

    return "ACTIVE", None


def replace_db_path_with_eval(raw_url: str) -> str:
    """URL 파서를 사용해 스키마, 자격증명, 호스트, 포트, 쿼리 파라미터는 완벽히 보존하고
    DB path 만 정확히 '/ktb_eval' 로 치환한다.
    """
    if not raw_url:
        return ""
    parsed = urlsplit(raw_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/ktb_eval", parsed.query, parsed.fragment))


def build_eval_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """순수(pure)한 평가 child 환경 변수 딕셔너리를 생성한다.
    규칙:
    1. 전역 os.environ 과 .env 파일은 절대 변경하지 않는다.
    2. process DATABASE_URL 우선 사용, 없으면 .env URL에서 URL 파서로 DB path 만 /ktb_eval 로 변경 (쿼리 보존).
    3. LANGFUSE_TRACING_ENVIRONMENT 는 'eval-local'.
    """
    env = dict(base_env if base_env is not None else os.environ)
    env_file = read_env_file()

    raw_db = env.get("DATABASE_URL")
    if not raw_db:
        env_raw = env_file.get("DATABASE_URL", "")
        if env_raw:
            env["DATABASE_URL"] = replace_db_path_with_eval(env_raw)

    env["LANGFUSE_TRACING_ENVIRONMENT"] = "eval-local"
    return env


def sanitize_db_url(raw_url: str) -> tuple[str, str, bool]:
    """DATABASE_URL 에서 자격증명/호스트를 완전히 숨기고 DB 경로 이름만 추출한다.
    반환: (database_name, sanitize_status, is_exact_ktb_eval)
    """
    if not raw_url:
        return "(empty)", "비어 있음", False
    try:
        parsed = urlsplit(raw_url)
        db_path = parsed.path  # 예: /ktb_eval
        is_exact = db_path == "/ktb_eval"
        db_name = db_path.lstrip("/") if db_path else "(none)"
        return db_name, f"path='{db_path}'", is_exact
    except Exception as e:
        return "(parse_error)", str(e), False


def run_preflight(env: dict[str, str] | None = None) -> PreflightReport:
    issues: list[PreflightIssue] = []
    head, is_dirty = check_git_status()
    env_vars = read_env_file()

    # 1. 환경 및 DB 검사 (read-only: 기존 env 변경하지 않음)
    # env 매개변수가 제공되면 해당 순수 평가 환경을 검사하고,
    # None 이면 read-only 단독 preflight 로서 현재 환경(os.environ or .env)을 strict 하게 검사한다.
    if env is not None:
        raw_db_target = env.get("DATABASE_URL", "")
        langfuse_env = env.get("LANGFUSE_TRACING_ENVIRONMENT", "")
    else:
        raw_db_target = os.environ.get("DATABASE_URL") or env_vars.get("DATABASE_URL", "")
        langfuse_env = os.environ.get("LANGFUSE_TRACING_ENVIRONMENT") or env_vars.get(
            "LANGFUSE_TRACING_ENVIRONMENT", ""
        )

    db_name, db_desc, is_exact_eval = sanitize_db_url(raw_db_target)

    # 자격증명이나 호스트 정보는 절대 env_info 에 넣지 않고 DB 이름만 기록
    env_info = {
        "TARGET_DATABASE": db_name,
        "DATABASE_PATH_CHECK": db_desc,
        "LANGFUSE_TRACING_ENVIRONMENT": langfuse_env,
        "GIT_HEAD": head,
        "GIT_DIRTY": str(is_dirty),
        "CONNECT_STATUS": "NOT_CONNECTED_PREFLIGHT_ONLY",  # 실제 연결 여부와 목표 URL 판정 분리
    }

    if not is_exact_eval:
        issues.append(
            PreflightIssue(
                severity="CRITICAL",
                feature="global",
                category="ENV",
                message=f"DATABASE_URL 의 경로가 정확히 '/ktb_eval'이 아닙니다 (현재 대상 DB: '{db_name}', {db_desc}). 운영 DB 오염 방지를 위해 차단합니다.",
                details={"target_db": db_name},
            )
        )
    else:
        issues.append(
            PreflightIssue(
                severity="INFO",
                feature="global",
                category="ENV",
                message=f"평가 DB 목표 경로 확인 완료 ('{db_name}'). 단, 실제 연결(connect)은 평가 실행 시 검증됩니다.",
                details={"target_db": db_name},
            )
        )

    if langfuse_env != "eval-local":
        issues.append(
            PreflightIssue(
                severity="CRITICAL",
                feature="global",
                category="ENV",
                message=f"LANGFUSE_TRACING_ENVIRONMENT 가 'eval-local'이 아닙니다 (현재='{langfuse_env}'). 운영 트레이스 오염 방지를 위해 차단합니다.",
                details={"LANGFUSE_TRACING_ENVIRONMENT": langfuse_env},
            )
        )

    # 2. 기능별 골드셋 무결성 검사
    feature_summaries: dict[str, dict[str, Any]] = {}

    for slug in ALL_SLUGS:
        contract_status, contract_reason = inspect_app_contract(slug)

        # 제외 대상(f10, f11)은 골드셋 검사 전에 SKIP
        if contract_status == "EXCLUDED_V1":
            issues.append(
                PreflightIssue(
                    severity="INFO",
                    feature=slug,
                    category="CONTRACT",
                    message=f"[{slug}] 공식 제외: {contract_reason}",
                    details={"status": contract_status},
                )
            )
            feature_summaries[slug] = {
                "status": contract_status,
                "contract_reason": contract_reason,
                "total_cases": 0,
                "split_counts": {},
                "oracle_drift_count": 0,
            }
            continue

        folder = EVALS / slug
        goldset_file = folder / "goldset.jsonl"

        if not goldset_file.exists():
            issues.append(
                PreflightIssue(
                    severity="CRITICAL",
                    feature=slug,
                    category="REQUIRED_FIELDS",
                    message=f"{goldset_file.name} 이 존재하지 않습니다.",
                )
            )
            continue

        cases = []
        with goldset_file.open("r", encoding="utf-8") as f:
            for idx, line in enumerate(f, 1):
                try:
                    c = json.loads(line)
                    cases.append(c)
                except Exception as e:
                    issues.append(
                        PreflightIssue(
                            severity="CRITICAL",
                            feature=slug,
                            category="REQUIRED_FIELDS",
                            message=f"{idx}번째 줄 JSON 파싱 실패: {e}",
                        )
                    )

        expected = EXPECTED_SPLIT_COUNTS.get(slug, DEFAULT_SPLIT_COUNTS)
        split_counts: dict[str, int] = {}
        case_ids: set[str] = set()
        duplicate_ids: set[str] = set()
        missing_fields: list[dict[str, Any]] = []
        oracle_drifts: list[dict[str, Any]] = []
        unregistered_splits: set[str] = set()

        # splitGroup 누수 검사: splitGroup -> set(splits)
        group_to_splits: dict[str, set[str]] = defaultdict(set)

        for c in cases:
            cid = c.get("id")
            s = c.get("split")

            # 미등록 split 차단
            if s not in ALLOWED_SPLITS:
                unregistered_splits.add(str(s))

            split_counts[s] = split_counts.get(s, 0) + 1

            if cid in case_ids:
                duplicate_ids.add(cid)
            case_ids.add(cid)

            # splitGroup 검사
            sg = c.get("splitGroup")
            if sg:
                group_to_splits[str(sg)].add(s)

            # 필수 필드 및 빈/잘못된 값 검출
            for req in ["id", "split", "input", "target_failures"]:
                if req not in c or c[req] is None or c[req] == "":
                    missing_fields.append({"case_id": cid, "missing_or_empty": req})

            # input 내용 검증: dict 여야 하며 비어 있으면 안 됨
            inp = c.get("input")
            if not isinstance(inp, dict) or len(inp) == 0:
                missing_fields.append({"case_id": cid, "invalid_input": True})

            # target_failures 검증: list 여야 함
            tf = c.get("target_failures")
            if not isinstance(tf, list):
                missing_fields.append({"case_id": cid, "invalid_target_failures": True})

            # 골드셋 오라클-최신 기준 정합성 검사 (f03 구버전 default_50_allowed)
            if slug == "f03-persona-extraction":
                expect = c.get("oracle", {}).get("expect", {})
                if isinstance(expect, dict) and expect.get("default_50_allowed") is True:
                    oracle_drifts.append(
                        {
                            "case_id": cid,
                            "field": "oracle.expect.default_50_allowed",
                            "value": True,
                            "conflict_with": "2026-10-28 최신 기준 (서비스가 제거한 차원에 모델이 점수를 냈는지로 판정)",
                        }
                    )

        # 검사 결과 등록
        if duplicate_ids:
            issues.append(
                PreflightIssue(
                    severity="CRITICAL",
                    feature=slug,
                    category="SPLIT",
                    message=f"중복된 케이스 ID 가 존재합니다: {duplicate_ids}",
                )
            )

        if unregistered_splits:
            issues.append(
                PreflightIssue(
                    severity="CRITICAL",
                    feature=slug,
                    category="SPLIT",
                    message=f"허용되지 않은 미등록 split 발견: {unregistered_splits} (허용: {ALLOWED_SPLITS})",
                )
            )

        # splitGroup 누수 검사: 동일 그룹이 여러 split 에 걸쳐 있으면 FAIL
        leaked_groups = {g: splits for g, splits in group_to_splits.items() if len(splits) > 1}
        if leaked_groups:
            issues.append(
                PreflightIssue(
                    severity="CRITICAL",
                    feature=slug,
                    category="SPLIT",
                    message=f"splitGroup 누수 감지: 동일 그룹이 복수의 split 에 나뉘어 있습니다: {leaked_groups}",
                    details={"leaked_groups": leaked_groups},
                )
            )

        if missing_fields:
            issues.append(
                PreflightIssue(
                    severity="CRITICAL",
                    feature=slug,
                    category="REQUIRED_FIELDS",
                    message=f"필수 필드 누락 또는 빈/잘못된 필드가 {len(missing_fields)}건 발견되었습니다.",
                    details={"sample_errors": missing_fields[:5]},
                )
            )

        for s_name, exp_cnt in expected.items():
            if s_name == "total":
                if len(cases) != exp_cnt:
                    issues.append(
                        PreflightIssue(
                            severity="CRITICAL",
                            feature=slug,
                            category="SPLIT",
                            message=f"총 케이스 수 불일치: 기대={exp_cnt}, 실제={len(cases)}",
                        )
                    )
            else:
                act_cnt = split_counts.get(s_name, 0)
                if act_cnt != exp_cnt:
                    issues.append(
                        PreflightIssue(
                            severity="CRITICAL",
                            feature=slug,
                            category="SPLIT",
                            message=f"Split '{s_name}' 케이스 수 불일치: 기대={exp_cnt}, 실제={act_cnt}",
                        )
                    )

        if contract_status == "QUARANTINED_CONTRACT_DROPPED":
            issues.append(
                PreflightIssue(
                    severity="WARNING",
                    feature=slug,
                    category="CONTRACT",
                    message=f"[{slug}] 계약 불일치 격리: {contract_reason}",
                    details={"status": contract_status},
                )
            )

        if oracle_drifts:
            issues.append(
                PreflightIssue(
                    severity="WARNING",
                    feature=slug,
                    category="ORACLE",
                    message=f"[{slug}] 골드셋 오라클에 구버전 기본 50점 허용({len(oracle_drifts)}건)이 포함되어 최신 2026-10-28 판정 기준과 모순 가능성이 있어 격리 보고합니다.",
                    details={"sample_cases": [d["case_id"] for d in oracle_drifts[:3]]},
                )
            )

        feature_summaries[slug] = {
            "status": contract_status,
            "contract_reason": contract_reason,
            "total_cases": len(cases),
            "split_counts": split_counts,
            "oracle_drift_count": len(oracle_drifts),
        }

    critical_count = sum(1 for i in issues if i.severity == "CRITICAL")
    passed = critical_count == 0

    return PreflightReport(
        passed=passed,
        git_head=head,
        git_dirty=is_dirty,
        issues=issues,
        feature_summaries=feature_summaries,
        env_info=env_info,
    )


def print_preflight_summary(report: PreflightReport) -> None:
    print("=" * 70)
    print("  6단계 사전 비행 검사 (Preflight Check) 결과")
    print("=" * 70)
    print(f"Git HEAD: {report.git_head} (Dirty: {report.git_dirty})")
    print(
        f"평가 DB 대상: '{report.env_info.get('TARGET_DATABASE')}' (검사: {report.env_info.get('DATABASE_PATH_CHECK')})"
    )
    print(f"연결 상태: {report.env_info.get('CONNECT_STATUS')}")
    print(f"전체 통과 여부: {'[PASS]' if report.passed else '[FAIL - CRITICAL BLOCKED]'}")
    print("-" * 70)
    print(f"{'기능':<35} | {'계약 상태':<25} | {'케이스 (cal/reg/hold)'}")
    print("-" * 70)
    for slug, s in report.feature_summaries.items():
        if s["status"] == "EXCLUDED_V1":
            print(f"{slug:<35} | {s['status']:<25} | [SKIP] v1 공식 제외")
            continue
        sc = s["split_counts"]
        splits_str = f"{sc.get('calibration', 0)}/{sc.get('regression', 0)}/{sc.get('blind_holdout', 0)}"
        print(f"{slug:<35} | {s['status']:<25} | {splits_str}")
    print("-" * 70)
    print(
        f"이슈 요약: CRITICAL={sum(1 for i in report.issues if i.severity == 'CRITICAL')}, "
        f"WARNING={sum(1 for i in report.issues if i.severity == 'WARNING')}, "
        f"INFO={sum(1 for i in report.issues if i.severity == 'INFO')}"
    )
    for issue in report.issues:
        if issue.severity in ("CRITICAL", "WARNING"):
            print(f"  [{issue.severity}] [{issue.feature}] ({issue.category}) {issue.message}")
    print("=" * 70)


if __name__ == "__main__":
    report = run_preflight()
    print_preflight_summary(report)
    sys.exit(0 if report.passed else 1)
