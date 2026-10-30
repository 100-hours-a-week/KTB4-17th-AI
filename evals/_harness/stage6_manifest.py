"""6단계 파일 내용 해시 기반 매니페스트 고정 (Manifest Freeze) 및 검증기

평가 재현에 필요한 모든 핵심 입력 파일을 자격증명/비밀 없이 해시하여 동결하고 검증한다.
- 대상:
  1. app/**/*.py (앱 코드, __pycache__ 제외)
  2. docs/성능평가_v2/**/*.md (평가 체계 및 기준서)
  3. evals/f*/goldset.jsonl (골드셋)
  4. evals/f*/labels/calibration_final.csv (확정 라벨)
  5. evals/f*/judges/*.md (Judge 템플릿)
  6. evals/_harness/*.py (실행 하네스 코드)
  7. evals/**/calibration_supplement.jsonl (보충 문제 데이터)

- 검증 (verify):
  1. 빈/위조 구조 방지
  2. Git HEAD 변경 감지 (현재 HEAD 와 매니페스트 commit 대조)
  3. 새 파일 추가 감지 (tracked 대상인데 매니페스트에 누락된 파일)
  4. 파일 누락 및 내용 변조/해시 불일치 감지
  5. 경로 + 해시 결합 combined_hash 무결성 검증

주의: 최종 공식 동결(freeze)은 워커가 아닌 코디네이터가 수행합니다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVALS = ROOT / "evals"
APP = ROOT / "app"
DOCS_EVAL = ROOT / "docs" / "성능평가_v2"
HARNESS = EVALS / "_harness"
DEFAULT_MANIFEST_PATH = EVALS / "manifest_b5e158c.json"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def line_count(p: Path) -> int:
    try:
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def collect_tracked_files() -> list[Path]:
    """재현에 필요한 모든 핵심 소스 및 평가 입력 파일을 수집한다."""
    files: set[Path] = set()

    # 1. app/**/*.py
    if APP.exists():
        for p in APP.rglob("*.py"):
            if "__pycache__" not in p.parts:
                files.add(p)

    # 2. docs/성능평가_v2/**/*.md
    if DOCS_EVAL.exists():
        for p in DOCS_EVAL.rglob("*.md"):
            files.add(p)

    # 3. 골드셋
    for p in EVALS.glob("f*/goldset.jsonl"):
        files.add(p)

    # 4. 확정 라벨
    for p in EVALS.glob("f*/labels/calibration_final.csv"):
        files.add(p)

    # 5. Judge 템플릿
    for p in EVALS.glob("f*/judges/*.md"):
        files.add(p)

    # 6. 하네스 도구들
    for p in HARNESS.glob("*.py"):
        if not p.name.startswith("."):
            files.add(p)

    # 7. supplements (보충 문제)
    for p in EVALS.rglob("*supplement*.jsonl"):
        files.add(p)

    return sorted(files)


def build_manifest() -> dict[str, Any]:
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    short_head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT
    ).stdout.strip()

    tracked_files = collect_tracked_files()
    entries = []
    total_bytes = 0
    total_cases = 0

    for p in tracked_files:
        rel = p.relative_to(ROOT).as_posix()
        sz = p.stat().st_size
        lc = line_count(p)
        h = sha256_file(p)
        total_bytes += sz

        cases_in_file = 0
        if p.name == "goldset.jsonl":
            cases_in_file = lc
            total_cases += cases_in_file

        entries.append(
            {
                "path": rel,
                "sha256": h,
                "size_bytes": sz,
                "lines": lc,
                "cases": cases_in_file if cases_in_file else None,
            }
        )

    # 경로 + 해시 결합 combined_hash
    entries = sorted(entries, key=lambda x: x["path"])
    combined_str = "\n".join(f"{e['path']}:{e['sha256']}" for e in entries)
    combined = hashlib.sha256(combined_str.encode("utf-8")).hexdigest()

    return {
        "version": "1.0.0",
        "frozen_at": datetime.now().isoformat(),
        "git_commit": head,
        "git_commit_short": short_head,
        "combined_hash": combined,
        "total_files": len(entries),
        "total_bytes": total_bytes,
        "total_goldset_cases": total_cases,
        "files": entries,
    }


def freeze_manifest(out_path: Path = DEFAULT_MANIFEST_PATH) -> Path:
    manifest = build_manifest()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def verify_manifest(manifest_path: Path = DEFAULT_MANIFEST_PATH) -> tuple[bool, list[str]]:
    if not manifest_path.exists():
        return False, [f"매니페스트 파일이 존재하지 않습니다: {manifest_path}"]

    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, [f"매니페스트 JSON 파싱 실패: {e}"]

    # 1. 빈/위조 구조 방지
    for req_field in ["version", "git_commit", "combined_hash", "files"]:
        if req_field not in data or not data[req_field]:
            return False, [f"매니페스트 구조 위조 또는 필수 필드 누락: '{req_field}'"]

    errors: list[str] = []

    # 2. Git HEAD 변경 감지
    cur_head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    recorded_head = data.get("git_commit", "")
    if cur_head and recorded_head and cur_head != recorded_head:
        errors.append(f"Git HEAD 불일치 (코드 변경 감지): 현재={cur_head[:8]}, 매니페스트={recorded_head[:8]}")

    # 3. 파일 목록 대조
    manifest_files_map = {e["path"]: e["sha256"] for e in data.get("files", [])}
    current_files = collect_tracked_files()
    current_files_set = {p.relative_to(ROOT).as_posix() for p in current_files}

    # 누락 파일 검증
    for rel_path, exp_hash in manifest_files_map.items():
        p = ROOT / rel_path
        if not p.exists():
            errors.append(f"기록된 파일 누락: {rel_path}")
            continue
        cur_hash = sha256_file(p)
        if cur_hash != exp_hash:
            errors.append(
                f"내용 변경 감지 (해시 불일치): {rel_path} (기대={exp_hash[:10]}..., 실제={cur_hash[:10]}...)"
            )

    # 새 파일 추가 감지 (매니페스트에 없는 새 파일)
    for cur_rel in current_files_set:
        if cur_rel not in manifest_files_map:
            errors.append(f"새 파일 추가 감지 (매니페스트 미등록): {cur_rel}")

    # 4. 결합 해시(combined_hash) 검증
    cur_entries = sorted(
        [{"path": p.relative_to(ROOT).as_posix(), "sha256": sha256_file(p)} for p in current_files],
        key=lambda x: x["path"],
    )
    cur_combined_str = "\n".join(f"{e['path']}:{e['sha256']}" for e in cur_entries)
    cur_combined = hashlib.sha256(cur_combined_str.encode("utf-8")).hexdigest()

    if cur_combined != data.get("combined_hash"):
        errors.append(
            f"결합 해시(combined_hash) 불일치: 기대={data.get('combined_hash')[:10]}..., 현재={cur_combined[:10]}..."
        )

    return (len(errors) == 0), errors


def main() -> None:
    ap = argparse.ArgumentParser(description="6단계 매니페스트 고정 및 검증")
    ap.add_argument("--freeze", action="store_true", help="현재 상태로 매니페스트 파일 생성")
    ap.add_argument("--verify", action="store_true", help="기존 매니페스트와 현재 파일 무결성 대조")
    ap.add_argument("--output", default=str(DEFAULT_MANIFEST_PATH), help="매니페스트 파일 경로")
    args = ap.parse_args()

    out_p = Path(args.output)
    if args.verify:
        ok, errs = verify_manifest(out_p)
        if ok:
            print(f"[PASS] 매니페스트 검증 완료: {out_p.name} 에 기록된 모든 파일 및 HEAD 가 완벽히 일치합니다.")
            sys.exit(0)
        else:
            print(f"[FAIL] 매니페스트 검증 실패 ({len(errs)}건 불일치):")
            for e in errs:
                print(f"  - {e}")
            sys.exit(1)

    freeze_manifest(out_p)
    print(f"[SUCCESS] 매니페스트 동결 완료: {out_p} (주의: 최종 공식 동결은 코디네이터가 수행합니다)")


if __name__ == "__main__":
    main()
