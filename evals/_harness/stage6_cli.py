"""6단계 통합 관리 CLI (Stage 6 Unified CLI)

사용법:
    # 1. 사전 비행 검사 (Preflight Check)
    python3 stage6_cli.py preflight

    # 2. 매니페스트 동결 (Manifest Freeze - 주의: 최종 동결은 코디네이터가 수행)
    python3 stage6_cli.py freeze [--output manifest.json]

    # 3. 매니페스트 무결성 검증 (Verify Manifest)
    python3 stage6_cli.py verify-manifest [--manifest manifest.json]

    # 4. 정적 모의 검증 (Dry-run: 로컬 파일 스키마 점검, LLM/DB 호출 없음)
    python3 stage6_cli.py dry-run

    # 5. 스코어카드 및 배포 게이트 판정 (Scorecard & Gates)
    python3 stage6_cli.py scorecard <result_file.json> [--json]

    # 6. 회귀 평가 실행 래퍼 (Preflight + Manifest Verify 통과 필수)
    python3 stage6_cli.py run-regression [--only f01,f02] [--limit 2] [--no-judge]

    # 7. 최종 배포 시험 래퍼 (Preflight + Manifest Verify 통과 및 --allow-holdout 필수)
    python3 stage6_cli.py run-holdout --allow-holdout [--only f01,f02]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EVALS = ROOT / "evals"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import stage6_manifest as M  # noqa: E402
import stage6_preflight as P  # noqa: E402
import stage6_scorecard as S  # noqa: E402


def cmd_preflight(args: argparse.Namespace) -> int:
    report = P.run_preflight()
    P.print_preflight_summary(report)
    return 0 if report.passed else 1


def cmd_freeze(args: argparse.Namespace) -> int:
    out_p = Path(args.output) if args.output else M.DEFAULT_MANIFEST_PATH
    M.freeze_manifest(out_p)
    print(f"[SUCCESS] 6단계 매니페스트 동결 완료: {out_p}")
    print("  * 주의: 최종 공식 동결(freeze)은 검토 후 코디네이터가 수행합니다.")
    return 0


def cmd_verify_manifest(args: argparse.Namespace) -> int:
    manifest_p = Path(args.manifest) if args.manifest else M.DEFAULT_MANIFEST_PATH
    ok, errs = M.verify_manifest(manifest_p)
    if ok:
        print(f"[PASS] 매니페스트 검증 성공: {manifest_p.name} 에 기록된 모든 파일 및 HEAD 가 일치합니다.")
        return 0
    else:
        print(f"[FAIL] 매니페스트 검증 실패 ({len(errs)}건 불일치):")
        for e in errs:
            print(f"  - {e}")
        return 1


def cmd_dry_run(args: argparse.Namespace) -> int:
    """16개 기능 대상 로컬 골드셋 스키마 및 파일 구조 정적 점검.
    (주의: 본 명령은 LLM API 호출, DB 인증 또는 실제 채점 준비 완료를 뜻하지 않습니다.)
    """
    print("=" * 70)
    print("  6단계 파이프라인 Dry-run (로컬 골드셋 및 스키마 정적 점검)")
    print("  * 주의: 본 검증은 LLM 호출이나 DB 인증, 채점 준비 완료를 뜻하지 않습니다.")
    print("=" * 70)

    # 1. Preflight 수행
    print("1. Preflight 검사 실행...")
    report = P.run_preflight()
    if not report.passed:
        print("[FAIL] Preflight 차단으로 dry-run 중단.")
        return 1
    print("   -> Preflight 통과.")

    # 2. Manifest 일치 확인
    print("2. Manifest 일치 검증...")
    if M.DEFAULT_MANIFEST_PATH.exists():
        ok, errs = M.verify_manifest(M.DEFAULT_MANIFEST_PATH)
        if not ok:
            print(f"   [WARN] Manifest 불일치 {len(errs)}건 (freeze 재실행 또는 HEAD 확인 필요)")
        else:
            print("   -> Manifest 무결성 일치 확인.")
    else:
        print("   -> Manifest 미생성 상태 (새로 freeze 필요).")

    # 3. 16개 기능별 골드셋 1번 케이스 읽기 및 스키마 검증
    print("3. 기능별 케이스 파싱 및 격리 상태 점검...")
    for slug, s in report.feature_summaries.items():
        status = s["status"]
        if status == "EXCLUDED_V1":
            print(f"   - {slug:<35}: [스킵] v1 미사용 기능")
            continue
        if status == "QUARANTINED_CONTRACT_DROPPED":
            print(f"   - {slug:<35}: [격리] 현재 HEAD 계약 제거로 자동 실행 차단됨")
            continue

        gs_file = EVALS / slug / "goldset.jsonl"
        with gs_file.open("r", encoding="utf-8") as f:
            first_case = json.loads(f.readline())

        print(f"   - {slug:<35}: [정상] 첫 문제 ID={first_case.get('id')}, split={first_case.get('split')}")

    print("=" * 70)
    print("[SUCCESS] 정적 dry-run 점검 완료 (실제 LLM/DB 실행은 코디네이터가 시작합니다).")
    print("=" * 70)
    return 0


def cmd_scorecard(args: argparse.Namespace) -> int:
    res_path = Path(args.result_file)
    if not res_path.exists():
        print(f"[FAIL] 결과 파일이 존재하지 않습니다: {res_path}")
        return 1

    manifest_p = Path(args.manifest) if args.manifest else None
    res = S.parse_and_score(res_path, manifest_p)
    card = res["scorecard"]
    gates = res["gates"]

    if args.json:
        out = {
            "card": card.__dict__,
            "gates": [g.__dict__ for g in gates],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(S.render_markdown_scorecard(card, gates))
    return 0


def get_eval_nonsecret_config(env_map: dict[str, str] | None = None) -> dict[str, str]:
    """자격증명, 토큰, 비밀번호를 일절 배제한 순수 모델 및 평가 설정 딕셔너리를 추출한다."""
    env = env_map if env_map is not None else os.environ
    env_file = P.read_env_file()

    def _val(key: str, default: str = "") -> str:
        return env.get(key) or env_file.get(key) or default

    raw_config = {
        "llm_model": _val("LLM_MODEL", "local-model"),
        "llm_model_v2": _val("LLM_MODEL_V2", "local-model"),
        "slm_tagging_model": _val("SLM_TAGGING_MODEL", ""),
        "slm_extraction_model": _val("SLM_EXTRACTION_MODEL", ""),
        "simulation_v2_judge_model": _val("SIMULATION_V2_JUDGE_MODEL", ""),
        "judge_model": _val("JUDGE_MODEL", "sonnet"),
        "langfuse_tracing_environment": _val("LANGFUSE_TRACING_ENVIRONMENT", "eval-local"),
    }

    # 비밀성 키워드 철저 검증 및 제거
    forbidden = ("key", "secret", "password", "token", "cred", "url")
    return {k: v for k, v in raw_config.items() if not any(f in k.lower() for f in forbidden)}


def compute_config_hash(cfg: dict[str, Any]) -> str:
    """비밀 없는 평가 구성 딕셔너리의 결정론적 sha256 fingerprint 계산."""
    payload = json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def prepare_eval_child_env(manifest_path: Path | None = None) -> dict[str, str]:
    """평가 child 프로세스에 전달할 순수 평가 환경 변수를 구성한다.
    - DATABASE_URL: process DATABASE_URL 우선 / 없으면 .env URL에서 /ktb_eval 로 치환 (쿼리 보존)
    - EVAL_MANIFEST_HASH: 현재 동결 매니페스트의 combined_hash
    - EVAL_GIT_COMMIT: full git commit
    - EVAL_NONSECRET_CONFIG: 비밀 없는 모델/평가 설정 JSON
    - EVAL_CONFIG_HASH: 위 설정의 fingerprint hash
    """
    mf = manifest_path or M.DEFAULT_MANIFEST_PATH
    eval_env = P.build_eval_env()

    # 1. Manifest Hash
    if mf.exists():
        try:
            mf_data = json.loads(mf.read_text(encoding="utf-8"))
            eval_env["EVAL_MANIFEST_HASH"] = mf_data.get("combined_hash", "")
        except Exception:
            eval_env["EVAL_MANIFEST_HASH"] = ""
    else:
        eval_env["EVAL_MANIFEST_HASH"] = ""

    # 2. Git Commit (full)
    try:
        full_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT
        ).stdout.strip()
    except Exception:
        full_commit = "unknown"
    eval_env["EVAL_GIT_COMMIT"] = full_commit

    # 3. Nonsecret configuration & fingerprint
    nonsecret_cfg = get_eval_nonsecret_config(eval_env)
    eval_env["EVAL_NONSECRET_CONFIG"] = json.dumps(nonsecret_cfg, ensure_ascii=False, sort_keys=True)
    eval_env["EVAL_CONFIG_HASH"] = compute_config_hash(nonsecret_cfg)

    return eval_env


def _verify_preconditions_before_run(
    manifest_path: Path | None = None,
    eval_env: dict[str, str] | None = None,
) -> bool:
    """실행 전 필수 전제조건 검증: Preflight 통과 및 Manifest 검증 필수."""
    print("[STAGE6] 실행 전 전제조건 검증 시작...")
    check_env = eval_env if eval_env is not None else P.build_eval_env()
    report = P.run_preflight(env=check_env)
    if not report.passed:
        print("[BLOCKED] Preflight 검사 미달로 실행이 차단되었습니다.")
        return False

    mf = manifest_path or M.DEFAULT_MANIFEST_PATH
    ok, errs = M.verify_manifest(mf)
    if not ok:
        print(f"[BLOCKED] 매니페스트 검증 실패 ({len(errs)}건)로 실행이 차단되었습니다:")
        for e in errs[:3]:
            print(f"  - {e}")
        return False

    print("[STAGE6] Preflight 및 Manifest 검증 성공. 하위 실행을 허가합니다.")
    return True


def cmd_run_regression(args: argparse.Namespace) -> int:
    """회귀(regression) 평가 안전 실행 래퍼 (Preflight + Manifest 검증 필수)."""
    if not _verify_preconditions_before_run():
        print("[ABORT] 전제조건 미충족으로 실제 프로세스를 호출하지 않고 종료합니다 (호출 0회).")
        return 1

    eval_env = prepare_eval_child_env()
    cmd = [
        sys.executable,
        str(HERE / "stage5_run.py"),
        "--split",
        "regression",
    ]
    if args.only:
        cmd.extend(["--only", args.only])
    if args.limit:
        cmd.extend(["--limit", str(args.limit)])
    if args.no_judge:
        cmd.append("--no-judge")
    if args.version:
        cmd.extend(["--version", args.version])
    if args.cases:
        cmd.extend(["--cases", args.cases])

    print(f"[STAGE6] 회귀 평가 실행: {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=ROOT, env=eval_env).returncode


def cmd_run_holdout(args: argparse.Namespace) -> int:
    """최종 시험(blind_holdout) 평가 안전 실행 래퍼 (Preflight + Manifest 검증 + --allow-holdout 필수)."""
    if not args.allow_holdout:
        print("[BLOCKED] blind_holdout 은 최종 배포 평가 전용입니다. --allow-holdout 플래그가 필수입니다.")
        return 1

    if not _verify_preconditions_before_run():
        print("[ABORT] 전제조건 미충족으로 실제 프로세스를 호출하지 않고 종료합니다 (호출 0회).")
        return 1

    eval_env = prepare_eval_child_env()
    cmd = [
        sys.executable,
        str(HERE / "stage5_run.py"),
        "--split",
        "blind_holdout",
        "--allow-holdout",
    ]
    if args.only:
        cmd.extend(["--only", args.only])
    if args.limit:
        cmd.extend(["--limit", str(args.limit)])
    if args.no_judge:
        cmd.append("--no-judge")
    if args.version:
        cmd.extend(["--version", args.version])

    print(f"[STAGE6] 최종 배포 시험(Blind Holdout) 실행: {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=ROOT, env=eval_env).returncode


def main() -> None:
    ap = argparse.ArgumentParser(description="6단계 기준선 및 배포 게이트 관리 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # preflight
    sub.add_parser("preflight", help="사전 비행 검사")

    # freeze
    p_freeze = sub.add_parser("freeze", help="매니페스트 동결")
    p_freeze.add_argument("--output", "-o", default="", help="출력 파일 경로")

    # verify-manifest
    p_ver = sub.add_parser("verify-manifest", help="매니페스트 무결성 검증")
    p_ver.add_argument("--manifest", "-m", default="", help="매니페스트 파일 경로")

    # dry-run
    sub.add_parser("dry-run", help="로컬 골드셋 정적 모의 점검")

    # scorecard
    p_sc = sub.add_parser("scorecard", help="스코어카드 및 게이트 판정")
    p_sc.add_argument("result_file", help="평가 결과 JSON 파일")
    p_sc.add_argument("--json", action="store_true", help="JSON 출력")
    p_sc.add_argument("--manifest", default="", help="대조할 매니페스트 경로")

    # run-regression
    p_reg = sub.add_parser("run-regression", help="회귀 평가 실행")
    p_reg.add_argument("--only", default="")
    p_reg.add_argument("--limit", type=int, default=0)
    p_reg.add_argument("--no-judge", action="store_true")
    p_reg.add_argument("--version", default="")
    p_reg.add_argument("--cases", default="")

    # run-holdout
    p_hld = sub.add_parser("run-holdout", help="최종 blind_holdout 평가 실행")
    p_hld.add_argument("--allow-holdout", action="store_true", required=True)
    p_hld.add_argument("--only", default="")
    p_hld.add_argument("--limit", type=int, default=0)
    p_hld.add_argument("--no-judge", action="store_true")
    p_hld.add_argument("--version", default="")

    args = ap.parse_args()

    table = {
        "preflight": cmd_preflight,
        "freeze": cmd_freeze,
        "verify-manifest": cmd_verify_manifest,
        "dry-run": cmd_dry_run,
        "scorecard": cmd_scorecard,
        "run-regression": cmd_run_regression,
        "run-holdout": cmd_run_holdout,
    }
    sys.exit(table[args.cmd](args))


if __name__ == "__main__":
    main()
