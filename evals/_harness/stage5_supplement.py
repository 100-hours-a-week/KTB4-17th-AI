"""5단계 희소 유형 Judge 검증용 합성 Fixture 모듈 (stage5_supplement.py).

- 표본 부족(정답 FAIL 1~2개) 9개 희소 유형에 대한 FAIL/PASS 대조 표본을 제공한다.
- 모든 fixture는 DB 조회 없이도 독립적인 판정이 가능하도록 self-contained grounding(프로필, fact, frequent_phrases, 대화 등)을 input 에 직접 포함한다.
- expected_verdict, target_type, description, fail_if 등은 Judge 프롬프트에 절대 누출하지 않는다.
- expected_verdict 는 '독립 검토 전 가설/초안(draft_expected)'으로 다루며, 2FAIL 2PASS가 표본 부족 문제를 완비/해결한 것으로 간주하지 않는다.
- CLI 기능:
    python3 stage5_supplement.py summary
    python3 stage5_supplement.py validate
    python3 stage5_supplement.py run --dry-run [--only slug]
    python3 stage5_supplement.py run [--only slug]  # 실제 LLM 호출 (현재는 호출 금지 유지)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import stage5_judges as J  # noqa: E402

EVALS = Path(__file__).resolve().parents[1]
RESULTS_SUPP_DIR = EVALS / "_harness/runs/supplement_results"

RARE_TYPES: dict[str, set[str]] = {
    "f01": {"F-04"},
    "f06": {"F-01", "F-07"},
    "f08": {"F-01"},
    "f12": {"F-06", "F-07", "F-10"},
    "f17": {"F-02"},
    "f18": {"F-02"},
}

REQUIRED_FIELDS = {
    "case_id",
    "slug",
    "target_type",
    "draft_expected",
    "is_synthetic",
    "fixture_category",
    "description",
    "input",
    "sample",
}


def supplement_path(slug: str) -> Path:
    return J.folder(slug) / "calibration_supplement.jsonl"


def validate_case(c: dict) -> list[str]:
    """단일 fixture 레코드의 스키마 및 규칙 무결성 검사."""
    errors = []
    missing = REQUIRED_FIELDS - set(c.keys())
    if missing:
        errors.append(f"필수 필드 누락: {sorted(list(missing))}")
        return errors

    if c.get("is_synthetic") is not True:
        errors.append("is_synthetic 은 반드시 True 여야 합니다.")
    if c.get("fixture_category") != "judge_calibration_supplement":
        errors.append("fixture_category 는 'judge_calibration_supplement' 여야 합니다.")

    slug = c.get("slug")
    if slug not in RARE_TYPES:
        errors.append(f"지원되지 않는 slug: {slug}")
    else:
        tt = c.get("target_type")
        if tt not in RARE_TYPES[slug]:
            errors.append(f"{slug}의 희소 유형 대상이 아님: {tt} (대상: {RARE_TYPES[slug]})")

    verd = c.get("draft_expected")
    if verd not in ("FAIL", "PASS"):
        errors.append(f"draft_expected 는 FAIL 또는 PASS 여야 함: {verd}")

    sample = c.get("sample")
    if not isinstance(sample, dict) or ("output_raw" not in sample and "output_parsed" not in sample):
        errors.append("sample 은 dict 형태이며 output_raw 또는 output_parsed 를 포함해야 함.")

    inp = c.get("input")
    if not isinstance(inp, dict):
        errors.append("input 은 dict 형태여야 함.")
    else:
        # DB 없는 self-contained grounding 검사
        if slug == "f06":
            if "partner_facts" not in inp or "partner_frequent_phrases" not in inp:
                errors.append("f06 input 에는 partner_facts 와 partner_frequent_phrases 가 필수입니다.")
        elif slug == "f12":
            if "a_facts" not in inp or "b_facts" not in inp:
                errors.append("f12 input 에는 a_facts 와 b_facts 가 필수입니다.")

    return errors


def load_supplements(slug: str | None = None) -> list[dict]:
    """보강 fixture 파일들을 로드."""
    slugs = [slug] if slug else list(RARE_TYPES.keys())
    all_cases = []
    for s in slugs:
        p = supplement_path(s)
        if not p.exists():
            continue
        for line_no, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                c = json.loads(line)
                errs = validate_case(c)
                if errs:
                    raise ValueError(f"[{s}:{line_no}] 검증 실패: {'; '.join(errs)}")
                all_cases.append(c)
            except Exception as e:
                raise ValueError(f"[{s}:{line_no}] JSON 파싱/검증 오류: {e}") from e
    return all_cases


def format_supplement_input(slug: str, inp: dict) -> str:
    """Judge 에 전달할 input 포맷팅 (정답 단서 없이 순수 사실 grounding 및 입력만 전달)."""
    # Judge에 넘길 때 정답 힌트가 될 수 있는 메타데이터 키는 일절 제외
    clean_inp = {
        k: v
        for k, v in inp.items()
        if k not in ("expected_verdict", "target_type", "description", "human_rationale", "fail_if")
    }

    if slug == "f06":
        facts_str = "\n".join("  · " + str(x) for x in clean_inp.get("partner_facts", [])) or "  (없음)"
        style_str = json.dumps(clean_inp.get("partner_frequent_phrases", []), ensure_ascii=False)
        profile_str = json.dumps(clean_inp.get("partner_profile", {}), ensure_ascii=False)
        msgs_str = json.dumps(clean_inp.get("messages", []), ensure_ascii=False, indent=1)
        return (
            f"연습 상대역 자료:\n[상대역]\n- 프로필: {profile_str}\n- 기억 fact:\n{facts_str}\n- 대화 스타일 문구(말투 예시일 뿐 사실 아님): {style_str}\n\n"
            f"대화 진행:\n{msgs_str}"
        )
    if slug == "f12":
        a_facts = "\n".join("  · " + str(x) for x in clean_inp.get("a_facts", [])) or "  (없음)"
        b_facts = "\n".join("  · " + str(x) for x in clean_inp.get("b_facts", [])) or "  (없음)"
        a_style = json.dumps(clean_inp.get("a_frequent_phrases", []), ensure_ascii=False)
        b_style = json.dumps(clean_inp.get("b_frequent_phrases", []), ensure_ascii=False)
        return (
            f"두 사람 자료:\n"
            f"[a ({clean_inp.get('nickname_a', 'a')})]\n- 프로필: {json.dumps(clean_inp.get('a_profile', {}), ensure_ascii=False)}\n"
            f"- 기억 fact:\n{a_facts}\n- 대화 스타일 문구(말투 예시일 뿐 사실 아님): {a_style}\n\n"
            f"[b ({clean_inp.get('nickname_b', 'b')})]\n- 프로필: {json.dumps(clean_inp.get('b_profile', {}), ensure_ascii=False)}\n"
            f"- 기억 fact:\n{b_facts}\n- 대화 스타일 문구(말투 예시일 뿐 사실 아님): {b_style}\n\n"
            f"대본 설정:\n총 턴수={clean_inp.get('turns')}"
        )
    return json.dumps(clean_inp, ensure_ascii=False, indent=1)


def build_judge_prompt_for_case(case: dict) -> tuple[str, dict]:
    """Judge 용 프롬프트를 구성하며 정답 누출이 없는지 검증."""
    slug = case["slug"]
    tid = case["target_type"]
    template_file = J.folder(slug) / "judges" / f"{tid}.md"
    if not template_file.exists():
        raise FileNotFoundError(f"Judge 템플릿 부재: {template_file}")

    template = template_file.read_text(encoding="utf-8")
    inp_formatted = format_supplement_input(slug, case["input"])
    sample = case["sample"]
    out_formatted = J.view_output(slug, sample)
    orc_formatted = "(공통 판정 기준만 적용)"  # 정답 암시 일절 없이 공통 기준만 전달

    inp_cut, inp_trunc = J.cut_with_flag(inp_formatted, 6000)
    out_cut, out_trunc = J.cut_with_flag(out_formatted, 6000)
    is_trunc = inp_trunc or out_trunc

    final_prompt = J.fill(template, inp_cut, out_cut, orc_formatted)

    # 비누출 검증: 프롬프트에 draft_expected 나 target_type 이 들어갔는지 확인
    # (단 템플릿 자체의 # Judge F-xx 헤더는 제외하고 fill 된 내용 검사)
    leak_warnings = []
    if case.get("description") and case["description"] in final_prompt:
        leak_warnings.append("description_leaked")

    meta = {
        "template_hash": hashlib.sha256(template.encode("utf-8")).hexdigest()[:16],
        "final_prompt_hash": hashlib.sha256(final_prompt.encode("utf-8")).hexdigest()[:16],
        "truncated": is_trunc,
        "input_truncated": inp_trunc,
        "output_truncated": out_trunc,
        "leak_warnings": leak_warnings,
        "input_full": inp_formatted,
        "output_full": out_formatted,
        "oracle_full": orc_formatted,
    }
    return final_prompt, meta


def summary_stats() -> dict[str, dict[str, dict[str, int]]]:
    """9개 희소 유형별 FAIL / PASS 표본 통계 반환."""
    stats: dict[str, dict[str, dict[str, int]]] = {}
    for slug, types in RARE_TYPES.items():
        stats[slug] = {t: {"FAIL": 0, "PASS": 0, "TOTAL": 0} for t in types}
        p = supplement_path(slug)
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)
            t = c.get("target_type")
            v = c.get("draft_expected")
            if t in stats[slug] and v in ("FAIL", "PASS"):
                stats[slug][t][v] += 1
                stats[slug][t]["TOTAL"] += 1
    return stats


def print_summary():
    stats = summary_stats()
    print("=== 9개 희소 유형 보강 Fixture 현황 (calibration_supplement.jsonl) ===")
    total_fail = 0
    total_pass = 0
    for slug, type_map in stats.items():
        for tid, counts in type_map.items():
            f = counts["FAIL"]
            p = counts["PASS"]
            tot = counts["TOTAL"]
            total_fail += f
            total_pass += p
            status = "대조군 구비(초안)" if f >= 2 and p >= 2 else "표본 부족"
            print(f"[{slug}:{tid}] FAIL: {f}개, PASS: {p}개 (총 {tot}개) — {status}")
    print(f"전체 합계: FAIL {total_fail}개, PASS {total_pass}개 (총 {total_fail + total_pass}개)")
    print(
        "※ 주의: 본 표본의 draft_expected 는 독립 검토 전 가설이며, 2FAIL 2PASS가 표본 부족 문제를 완비/해결한 것은 아닙니다."
    )


def run_evaluation(
    slug: str | None = None,
    dry_run: bool = False,
    workers: int = 3,
) -> dict:
    """합성 fixture 에 대해 판정 평가 실행 (dry-run 은 파일 미저장, 실run 은 격리 저장)."""
    cases = load_supplements(slug)
    results = []

    print(f"[{'DRY-RUN' if dry_run else 'RUN'}] {len(cases)}개 fixture 판정 준비 중 (대상 slug: {slug or '전체'})...")

    if dry_run:
        plan = []
        for c in cases:
            prompt, meta = build_judge_prompt_for_case(c)
            plan.append(
                {
                    "case_id": c["case_id"],
                    "slug": c["slug"],
                    "target_type": c["target_type"],
                    "draft_expected": c["draft_expected"],
                    "prompt_hash": meta["final_prompt_hash"],
                    "truncated": meta["truncated"],
                    "leak_warnings": meta["leak_warnings"],
                }
            )
        print(f"[DRY-RUN PLAN] 총 {len(plan)}개 fixture 프롬프트 구성 완료 (파일 미저장, LLM 호출 0회).")
        return {"plan": plan, "dry_run": True, "count": len(plan)}

    # 실실행 모드 (현재는 LLM 호출 금지 지침 유지)
    RESULTS_SUPP_DIR.mkdir(parents=True, exist_ok=True)
    now_dt = datetime.now(UTC)
    stamp = now_dt.strftime("%Y%m%d-%H%M%S")

    with ThreadPoolExecutor(workers) as pool:
        futures = []
        for c in cases:
            prompt, meta = build_judge_prompt_for_case(c)
            if meta["truncated"]:
                # 잘림 발생 시 모델 미호출 ERROR
                res = {"verdict": "ERROR", "reasoning": "입출력 6000자 초과 잘림 발생", "truncated": True}
                futures.append((c, meta, None, res))
            else:
                fut = pool.submit(J.call, prompt)
                futures.append((c, meta, fut, None))

        for c, meta, fut, immediate_res in futures:
            res = fut.result() if fut else immediate_res
            results.append(
                {
                    "case_id": c["case_id"],
                    "slug": c["slug"],
                    "target_type": c["target_type"],
                    "draft_expected": c["draft_expected"],
                    "judge_result": res,
                    "meta": meta,
                }
            )

    # 지표 집계 (분모 정직 구분)
    tp = fn = tn = fp = err = 0
    for r in results:
        exp = r["draft_expected"]
        act = r["judge_result"].get("verdict")
        if act == "ERROR" or act not in ("FAIL", "PASS"):
            err += 1
        elif exp == "FAIL":
            if act == "FAIL":
                tp += 1
            else:
                fn += 1
        elif exp == "PASS":
            if act == "PASS":
                tn += 1
            else:
                fp += 1

    valid = tp + fn + tn + fp
    total = len(results)
    out_file = RESULTS_SUPP_DIR / f"{stamp}_supplement_eval_{slug or 'all'}.json"

    eval_data = {
        "evaluated_at": now_dt.isoformat(),
        "total_cases": total,
        "valid_judged_cases": valid,
        "error_cases": err,
        "tp": tp,
        "fn": fn,
        "tn": tn,
        "fp": fp,
        "valid_tpr": (tp / (tp + fn)) if (tp + fn) else None,
        "valid_tnr": (tn / (tn + fp)) if (tn + fp) else None,
        "accuracy_valid": ((tp + tn) / valid) if valid else None,
        "rows": results,
    }

    out_file.write_text(json.dumps(eval_data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[실행 완료] 결과 저장: {out_file.name} (전체 {total}건 중 유효 {valid}건, ERROR {err}건)")
    return eval_data


def main():
    ap = argparse.ArgumentParser(description="희소 유형 합성 Judge Fixture 관리 및 평가 도구")
    ap.add_argument("action", choices=["summary", "validate", "run"], default="summary", nargs="?")
    ap.add_argument("--dry-run", action="store_true", help="LLM 호출 및 결과 파일 작성 없이 계획만 검증")
    ap.add_argument("--only", default="", help="특정 slug 만 대상 지정")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()

    if a.action == "validate":
        cases = load_supplements(a.only or None)
        print(f"성공: {len(cases)}개 fixture 스키마, self-contained grounding 검증 통과.")
        print_summary()
    elif a.action == "run":
        run_evaluation(slug=a.only or None, dry_run=a.dry_run, workers=a.workers)
    else:
        print_summary()


if __name__ == "__main__":
    main()
