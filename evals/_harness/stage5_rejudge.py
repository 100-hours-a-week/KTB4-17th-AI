"""Judge 판정만 다시 — results 의 최신 실행에서 ERROR(구독 한도 등) 또는 누락된 유형을 다시 부른다. 앱은 돌리지 않는다.

- 기존 결과 파일 비파괴: 기존 결과 파일을 절대 덮어쓰지 않고, 고유 타임스탬프와 UUID가 부여된 새 파일({stamp}_{uuid}_{ver}_{split}.json)로 저장.
- latest_result 호환: 새 파일이 '*_{split}.json' 규칙을 만족하여 stage5_labels 및 후속 단계에서 최신 결과로 즉시 소비/재개 가능.
- dry-run 무결성: dry-run 모드에서는 판정 결과 파일을 절대 생성하지 않고 실행 계획만 반환/출력.
- 스냅샷 완전성: 500자 축약이 아닌 input_full, output_full, oracle_full, template_hash, final_prompt_hash를 기록.
- 사전 차단(Pre-flight): 빈 template, 실행 오류 결측 출력, 누락 케이스, context 결측, 6000자 초과 잘림 발생 시 모델 호출 없이 ERROR 처리 (정상 PASS 금지).
- historical provenance: 과거 결과 복원 시 lf_eval_cache.json 매칭을 시도하고, 미관측 결측 시 명시적 차단.
- holdout 보호: --split blind_holdout 은 --allow-holdout 없이 실행 불가하며, holdout 에서는 강제 유형 보정(--types) 금지.

사용:
    python3 stage5_rejudge.py [--split calibration] [--only f01,f03] [--workers 3]
    python3 stage5_rejudge.py --types f01:F-01,f03:F-02   # 지정한 유형은 ERROR/누락이 아니어도 전부 다시 판정 (Judge 보정용)
    python3 stage5_rejudge.py --dry-run                  # 실제 LLM 호출 및 파일 저장 없이 계획만 출력
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import stage3_types as S  # noqa: E402
import stage5_judges as J  # noqa: E402
from stage5_labels import goldset  # noqa: E402

EVALS = Path(__file__).resolve().parents[1]
LF_CACHE_PATH = EVALS / "_harness/runs/lf_eval_cache.json"

_LF_CACHE: dict | None = None


def get_lf_cache() -> dict:
    global _LF_CACHE
    if _LF_CACHE is None:
        if LF_CACHE_PATH.exists():
            try:
                _LF_CACHE = json.loads(LF_CACHE_PATH.read_text(encoding="utf-8"))
            except Exception:
                _LF_CACHE = {}
        else:
            _LF_CACHE = {}
    return _LF_CACHE


def find_lf_trace(slug: str, case_id: str, sample: dict) -> dict | None:
    """lf_eval_cache.json 에서 과거 관측 trace 를 exact source match 로 탐색."""
    cache = get_lf_cache()
    if not cache:
        return None
    # slug 매핑
    feature_map = {
        "f01": "persona-conversation",
        "f02": "persona-tagging",
        "f03": "persona-extraction",
        "f04": "persona-extraction-style",
        "f05": "v2-persona-extraction",
        "f06": "practice-reply",
        "f07": "v2-practice-speech-permission",
        "f08": "v2-memory-extract",
        "f09": "v2-memory-reconcile",
        "f12": "simulation-v2-generate-text",
        "f13": "simulation-v2-regenerate-text",
        "f14": "simulation-v2-judge",
        "f15": "simulation-v2-repair",
        "f16": "simulation-v2-report",
        "f17": "chat-end-drafts",
        "f18": "chat-end-message",
    }
    feat = feature_map.get(slug)
    if not feat or feat not in cache:
        return None

    sid = sample.get("session_id") or sample.get("id")
    traces = cache[feat]
    for tr in traces:
        if sid and (tr.get("session_id") == sid or tr.get("id") == sid):
            return tr
    return None


def verdicts(slug: str, codes: dict, judges: dict) -> dict[str, str]:
    v = {}
    for t in S.D[slug]["types"]:
        tid = t["id"]
        if tid in judges and judges[tid].get("verdict"):
            v[tid] = judges[tid]["verdict"]
        elif tid in codes and codes[tid].get("pass") is not None:
            v[tid] = "PASS" if codes[tid]["pass"] else "FAIL"
        else:
            v[tid] = "NA"
    return v


def compute_prompt_hashes(slug: str) -> dict[str, str]:
    hashes = {}
    d = J.folder(slug) / "judges"
    for t in J.judge_types(slug):
        tid = t["id"]
        p = d / f"{tid}.md"
        if p.exists():
            hashes[tid] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    return hashes


def find_todo_types(slug: str, row: dict, force_types: set[str] | None = None) -> list[str]:
    """해당 row에서 판정해야 할 Judge 유형 목록을 반환 (ERROR, 강제 지정, no-judge로 누락된 유형)."""
    force_types = force_types or set()
    judge_map = row.get("judge") or {}
    all_required = [t["id"] for t in J.judge_types(slug)]

    todo = []
    for tid in all_required:
        if tid in force_types:
            todo.append(tid)
        elif tid not in judge_map:
            # no-judge 또는 이전 실행에서 누락된 유형
            todo.append(tid)
        elif judge_map[tid].get("verdict") == "ERROR":
            todo.append(tid)
    return todo


def evaluate_single_judge(
    slug: str,
    tid: str,
    case: dict | None,
    sample: dict | None,
) -> tuple[dict, dict, bool]:
    """단일 Judge 사전 검증, 판정 실행 및 전체 스냅샷 반환."""
    # Pre-flight Check 1: case 또는 sample 누락
    if not case:
        res = {"verdict": "ERROR", "reasoning": "골드셋에 해당 케이스가 존재하지 않음"}
        snap = {"error": "CASE_MISSING"}
        return res, snap, False

    sample = sample or {}

    # Pre-flight Check 2: 모델 실행 오류로 인한 출력 결측
    if sample.get("error") and sample.get("output_raw") is None and sample.get("output_parsed") is None:
        err_msg = str(sample.get("error"))
        res = {"verdict": "ERROR", "reasoning": f"모델 실행 오류로 인한 출력 결측: {err_msg}"}
        snap = {"error": "OUTPUT_MISSING_DUE_TO_EXEC_ERROR", "model_error": err_msg}
        return res, snap, False

    # Pre-flight Check 3: 템플릿 파일 부재 또는 빈 템플릿
    judge_file = J.folder(slug) / "judges" / f"{tid}.md"
    if not judge_file.exists():
        res = {"verdict": "ERROR", "reasoning": f"Judge 프롬프트 파일 부재: {judge_file.name}"}
        snap = {"error": "TEMPLATE_MISSING"}
        return res, snap, False

    template = judge_file.read_text(encoding="utf-8")
    if not template.strip():
        res = {"verdict": "ERROR", "reasoning": f"Judge 프롬프트 파일이 비어있음: {judge_file.name}"}
        snap = {"error": "TEMPLATE_EMPTY"}
        return res, snap, False

    template_hash = hashlib.sha256(template.encode("utf-8")).hexdigest()[:16]

    # Context 및 입출력 구성
    inp = J.view_input(slug, case.get("input"), sample)
    out = J.view_output(slug, sample)
    orc = J.oracle_text(case)

    # Pre-flight Check 4: 필수 context 결측 여부 점검 (DB 미연결 등)
    # lf_eval_cache 탐색 시도
    lf_trace = find_lf_trace(slug, case.get("id", ""), sample)
    lf_matched = lf_trace is not None
    lf_trace_id = lf_trace.get("id") if lf_trace else None

    # 만약 f03에서 대화 내역이 완전히 결측되어 있고 fallback도 없는 경우 차단
    if slug == "f03" and "대화 내역 누락" in inp:
        res = {"verdict": "ERROR", "reasoning": "온보딩 대화 내역이 결측되어 판정 불가 (DB 미연결 및 캐시 부재)"}
        snap = {
            "template_hash": template_hash,
            "error": "REQUIRED_CONTEXT_MISSING",
            "historical_context_unobserved": True,
        }
        return res, snap, False

    # Pre-flight Check 5: Truncation 검사 (6000자 초과)
    inp_cut, inp_truncated = J.cut_with_flag(inp, 6000)
    out_cut, out_truncated = J.cut_with_flag(out, 6000)
    is_truncated = inp_truncated or out_truncated

    final_prompt = J.fill(template, inp_cut, out_cut, orc)
    final_prompt_hash = hashlib.sha256(final_prompt.encode("utf-8")).hexdigest()[:16]

    snapshot = {
        "template_hash": template_hash,
        "final_prompt_hash": final_prompt_hash,
        "input_full": inp,
        "output_full": out,
        "oracle_full": orc,
        "truncated": is_truncated,
        "input_truncated": inp_truncated,
        "output_truncated": out_truncated,
        "historical_cache_matched": lf_matched,
        "lf_trace_id": lf_trace_id,
    }

    if is_truncated:
        # 잘림 발생 시 모델을 호출하지 않고 ERROR 처리 (정상 PASS 금지)
        res = {
            "verdict": "ERROR",
            "reasoning": f"입력 또는 출력이 6000자를 초과하여 잘림(truncation) 발생 (입력잘림={inp_truncated}, 출력잘림={out_truncated})",
            "truncated": True,
        }
        return res, snapshot, True

    # 실제 LLM 판정 호출 (주간한도 등 감지 포함)
    res = J.call(final_prompt)
    return res, snapshot, False


def plan_rejudge(slug: str, res_path: Path, force_types: set[str] | None = None) -> dict:
    """dry-run 용 계획 생성 (파일 작성 없음, 모델 호출 없음)."""
    source_bytes = res_path.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    d = json.loads(source_bytes.decode("utf-8"))
    gs = goldset(slug)
    prompt_hashes = compute_prompt_hashes(slug)

    force_types = force_types or set()
    rows_plan = []
    total_todo = 0

    for r in d.get("rows", []):
        cid = r["case_id"]
        c = gs.get(cid)
        s = r.get("sample", {})
        todo = find_todo_types(slug, r, force_types)
        total_todo += len(todo)
        rows_plan.append(
            {
                "case_id": cid,
                "case_exists": c is not None,
                "sample_error": s.get("error"),
                "todo_types": todo,
            }
        )

    return {
        "slug": slug,
        "source_file": res_path.name,
        "source_sha256": source_sha,
        "total_rows": len(rows_plan),
        "total_todo_judges": total_todo,
        "target_force_types": sorted(list(force_types)),
        "available_prompt_hashes": prompt_hashes,
        "rows": rows_plan,
        "dry_run": True,
    }


def rejudge_result_file(
    slug: str,
    res_path: Path,
    split: str = "calibration",
    force_types: set[str] | None = None,
    workers: int = 3,
    out_path: Path | None = None,
) -> tuple[Path, int, int]:
    """단일 결과 파일에 대해 rejudge를 수행하고 비파괴적으로 새 결과 파일({stamp}_{uuid}_{ver}_{split}.json)을 생성."""
    source_bytes = res_path.read_bytes()
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    d = json.loads(source_bytes.decode("utf-8"))
    gs = goldset(slug)
    prompt_hashes = compute_prompt_hashes(slug)

    force_types = force_types or set()
    total_todo = 0

    rows = d.get("rows", [])
    with ThreadPoolExecutor(workers) as pool:
        for r in rows:
            r.setdefault("judge", {})
            r.setdefault("code", {})
            r.setdefault("judge_snapshots", {})
            r.setdefault("truncation_warnings", {})

            cid = r["case_id"]
            c = gs.get(cid)
            s = r.get("sample", {})

            todo = find_todo_types(slug, r, force_types)
            if not todo:
                continue

            total_todo += len(todo)

            futures = {tid: pool.submit(evaluate_single_judge, slug, tid, c, s) for tid in todo}
            for tid, fut in futures.items():
                res, snap, trunc = fut.result()
                r["judge"][tid] = res
                r["judge_snapshots"][tid] = snap
                if trunc:
                    r["truncation_warnings"][tid] = {
                        "input_truncated": snap.get("input_truncated", False),
                        "output_truncated": snap.get("output_truncated", False),
                    }

            r["verdict"] = verdicts(slug, r["code"], r["judge"])

    # 새 결과 파일 경로 결정 (기존 파일 절대 덮어쓰기 금지)
    now_dt = datetime.now(UTC)
    now_stamp = now_dt.strftime("%Y%m%d-%H%M%S")
    rand_uuid = uuid.uuid4().hex[:8]
    ver = d.get("version", "unknown")

    if out_path:
        # 명시된 out_path가 소스 파일이거나 이미 존재하면 즉시 실패
        if out_path.resolve() == res_path.resolve():
            raise ValueError(f"덮어쓰기 금지: 출력 경로가 소스 파일과 동일합니다: {out_path}")
        if out_path.exists():
            raise FileExistsError(f"덮어쓰기 금지: 대상 출력 파일이 이미 존재합니다: {out_path}")
        target_path = out_path
    else:
        # *_calibration.json 구조 유지
        new_filename = f"{now_stamp}_{rand_uuid}_{ver}_{split}.json"
        target_path = res_path.parent / new_filename

    # 부분 재판정이면 갱신된 유형과 갱신되지 않은 유형의 provenance를 정직하게 분리
    existing_prov = d.get("rejudge_provenance", {})
    type_provenance = {}
    for tid in prompt_hashes:
        if tid in force_types or not existing_prov:
            type_provenance[tid] = {
                "prompt_hash": prompt_hashes[tid],
                "updated_at": now_dt.isoformat(),
                "status": "rejudged" if tid in force_types else "active",
            }
        else:
            old_types = existing_prov.get("type_provenance", {})
            if tid in old_types:
                type_provenance[tid] = old_types[tid]
            else:
                type_provenance[tid] = {
                    "prompt_hash": existing_prov.get("prompt_hashes", {}).get(tid, "historical_unmodified"),
                    "updated_at": existing_prov.get("rejudged_at", "historical"),
                    "status": "historical_unmodified",
                }

    # provenance 기록
    d["rejudge_provenance"] = {
        "rejudged_at": now_dt.isoformat(),
        "source_result_file": res_path.name,
        "source_result_sha256": source_sha,
        "judge_model": J.JUDGE_MODEL,
        "prompt_hashes": prompt_hashes,
        "type_provenance": type_provenance,
        "rejudge_types": sorted(list(force_types)),
        "split": split,
        "total_rejudged_count": total_todo,
        "dry_run": False,
    }

    target_path.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")

    left_error = sum(1 for r in rows for v in r.get("judge", {}).values() if v.get("verdict") == "ERROR")
    return target_path, total_todo, left_error


def main() -> None:
    ap = argparse.ArgumentParser(description="Judge 비파괴 재판정 도구")
    ap.add_argument("--split", default="calibration")
    ap.add_argument("--allow-holdout", action="store_true", help="최종 측정 시 blind_holdout 허용 플래그")
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--types", default="", help="slug:F-xx,... — 이 유형은 모든 문제를 다시 판정")
    ap.add_argument("--dry-run", action="store_true", help="결과 파일 작성 없이 계획만 출력")
    a = ap.parse_args()

    # blind_holdout 보호
    if a.split == "blind_holdout":
        if not a.allow_holdout:
            sys.exit("오류: blind_holdout 은 보정 재판정에 허용되지 않습니다 (--allow-holdout 필요).")
        if a.types:
            sys.exit("오류: blind_holdout 에서는 특정 유형 강제 보정(--types)이 금지됩니다.")

    force: dict[str, set[str]] = {}
    for x in filter(None, a.types.split(",")):
        sl, t = x.split(":")
        force.setdefault(sl, set()).add(t)
    if force and not a.only:
        a.only = ",".join(force)

    for slug in S.D:
        if slug in ("f10", "f11"):
            continue
        if a.only and slug not in a.only.split(","):
            continue
        # smoke, dryrun, plan, test 제외하고 가장 최근 실제 결과 파일 탐색
        ps = sorted(
            p
            for p in (J.folder(slug) / "results").glob(f"*_{a.split}.json")
            if not any(x in p.name.lower() for x in ("smoke", "dryrun", "dry_run", "plan", "test"))
        )
        if not ps:
            continue
        p = ps[-1]

        if a.dry_run:
            plan = plan_rejudge(slug, p, force_types=force.get(slug, set()))
            print(
                f"[DRY-RUN PLAN: {slug}] 파일: {p.name}, 대상 문제: {plan['total_rows']}, 재판정 대상 Judge: {plan['total_todo_judges']}건 (결과 파일 저장 없음)"
            )
            continue

        out_path, todo, left = rejudge_result_file(
            slug=slug,
            res_path=p,
            split=a.split,
            force_types=force.get(slug, set()),
            workers=a.workers,
        )
        print(f"[{slug}] 재판정 {todo}건 처리, 남은 ERROR: {left}건 -> 새 파일: {out_path.name}", flush=True)
        if J.LIMIT_HIT.is_set():
            print("구독 한도 — 멈춤. 한도가 풀리면 다시 실행하세요.")
            break


if __name__ == "__main__":
    main()
