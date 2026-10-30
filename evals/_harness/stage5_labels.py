"""5단계 라벨링 — 시트 만들기, Codex 채점, 사람 간 일치도, Judge 보정 지표.

    python3 stage5_labels.py sheet                 # results 의 최신 calibration 실행으로 labels/calibration_template.csv
    python3 stage5_labels.py codex [--only f01]     # Codex(두 번째 채점자)로 labels/calibration_codex.csv
    python3 stage5_labels.py agree                  # labels/calibration_user.csv vs calibration_codex.csv → 5-2 불일치 목록
    python3 stage5_labels.py calib                  # 합의 라벨(labels/calibration_final.csv) vs Judge·코드 → 5-3 TPR/TNR

라벨 값: FAIL / PASS / NA(이 문제에 해당 없음). 빈칸은 NA 로 본다.
시트에는 Judge·코드 검사 결과를 넣지 않는다 (채점자가 미리 보지 않게).
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import stage3_types as S  # noqa: E402
import stage5_judges as J  # noqa: E402

EVALS = Path(__file__).resolve().parents[1]
EXCLUDED_SLUGS = {"f10", "f11"}


def compute_prompt_hash(slug: str, tid: str) -> str | None:
    """Judge 유형 프롬프트 템플릿의 SHA-256 해시 앞 16자리 계산."""
    if slug in EXCLUDED_SLUGS:
        return None
    try:
        p = J.folder(slug) / "judges" / f"{tid}.md"
        if p.exists():
            return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    except Exception:
        return None
    return None


def latest_result(slug: str, split: str = "calibration") -> dict | None:
    if slug in EXCLUDED_SLUGS:
        return None
    ps = sorted((J.folder(slug) / "results").glob(f"*_{split}.json"))
    ps = [p for p in ps if not any(x in p.name.lower() for x in ("smoke", "dryrun", "dry_run", "plan", "test"))]
    return json.loads(ps[-1].read_text()) if ps else None


def goldset(slug: str) -> dict[str, dict]:
    if slug in EXCLUDED_SLUGS:
        return {}
    return {
        c["id"]: c for c in (json.loads(x) for x in (J.folder(slug) / "goldset.jsonl").read_text().splitlines() if x)
    }


def type_ids(slug: str) -> list[str]:
    return [t["id"] for t in S.D[slug]["types"]]


# ── 시트 ─────────────────────────────────────────────────────────


def sheet() -> None:
    for slug in S.D:
        if slug in EXCLUDED_SLUGS:
            continue
        res = latest_result(slug)
        if not res:
            print(slug, "실행 결과 없음")
            continue
        gs = goldset(slug)
        d = J.folder(slug) / "labels"
        d.mkdir(exist_ok=True)
        tids = type_ids(slug)
        with (d / "calibration_template.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["case_id", "입력 요약", "출력", "정답 기준", *tids, "메모"])
            for r in res["rows"]:
                c = gs[r["case_id"]]
                s = r["sample"]
                out = J.view_output(slug, s) if s.get("output_raw") is not None or s.get("output_parsed") else ""
                if s.get("error"):
                    out = f"[실행 오류] {s['error']}\n{out}"
                w.writerow(
                    [
                        r["case_id"],
                        J.cut(J.view_input(slug, c["input"]), 3000),
                        J.cut(out, 4000),
                        J.oracle_text(c),
                        *[""] * len(tids),
                        "",
                    ]
                )
        guide = [
            f"# {J.title(slug)} — 검증용 라벨링 안내",
            "",
            "`calibration_template.csv` 를 복사해 `calibration_user.csv` 로 저장하고, 유형 칸에 FAIL / PASS / NA 를 적는다.",
            "- FAIL: 이 출력에 그 실패가 있다. PASS: 없다. NA: 이 문제·출력에는 해당 없음 (빈칸도 NA).",
            "- '정답 기준'은 골드셋에 적어 둔 기대값이다. 출력을 직접 보고 판단한다.",
            "- 다른 채점자(Codex) 시트·Judge 결과는 채점이 끝날 때까지 보지 않는다.",
            "",
            "## 실패 유형",
            "",
            *[
                f"- **{t['id']} {S.ko_name(t['name'])}** ({S.ko_sev(t['sev'])}) — {t['defn']} FAIL 기준: {t['fail']}"
                for t in S.D[slug]["types"]
            ],
            "",
        ]
        (d / "README.md").write_text("\n".join(guide))
        print(slug, len(res["rows"]), "행")


# ── Codex 채점 ───────────────────────────────────────────────────

CODEX_INSTR = """당신은 소개팅 앱 AI 기능의 출력을 채점하는 사람 검토자 역할입니다. 파일을 고치지 마세요.
아래 실패 유형표와 확정 판정 기준으로, 각 문제의 '출력'에 각 실패가 있는지 판정합니다.
값: FAIL(그 실패가 있다) / PASS(없다) / NA(이 출력에는 해당 없음, 예: 출력이 비었거나 그 유형을 판단할 내용이 없음).
'정답 기준'은 골드셋 작성자의 기대값입니다. 참고하되 출력을 직접 보고 판단하세요.
출력은 JSON 하나만: {"labels": {"<case_id>": {"F-01": "PASS", ...}, ...}}
"""


def codex(only: str = "", engine: str = "codex") -> None:
    jobs = []
    for slug in S.D:
        if slug in EXCLUDED_SLUGS:
            continue
        if only and slug not in only.split(","):
            continue
        p = J.folder(slug) / "labels" / "calibration_template.csv"
        if not p.exists():
            continue
        rows = list(csv.DictReader(p.open(encoding="utf-8-sig")))
        types = "\n".join(
            f"- {t['id']} `{t['name']}` ({t['sev']}): {t['defn']} / FAIL 기준: {t['fail']}" for t in S.D[slug]["types"]
        )
        body = "\n\n".join(
            f"### 문제 {r['case_id']}\n입력:\n{r['입력 요약']}\n\n출력:\n{r['출력']}\n\n정답 기준:\n{r['정답 기준']}"
            for r in rows
        )
        prompt = f"{CODEX_INSTR}\n## 기능: {J.title(slug)}\n\n## 확정 판정 기준\n{J.CONFIRMED}\n\n## 실패 유형표\n{types}\n\n## 문제\n\n{body}\n"
        jobs.append((slug, prompt, rows))

    def run(job):
        slug, prompt, rows = job
        out = J.folder(slug) / "labels" / f"{engine}_raw.json"
        if engine == "codex":
            r = subprocess.run(
                ["codex", "exec", "-s", "read-only", "--skip-git-repo-check", "--ephemeral", "-o", str(out), "-"],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=3600,
            )
        else:  # grok: 프롬프트 파일로 넘기고 표준 출력을 받는다
            pf = J.folder(slug) / "labels" / "grok_prompt.md"
            pf.write_text(prompt)
            r = subprocess.run(
                ["grok", "--prompt-file", str(pf), "--disable-web-search"],
                capture_output=True,
                text=True,
                timeout=3600,
            )
            out.write_text(r.stdout)
            pf.unlink(missing_ok=True)
        try:
            labels = json.loads(re.search(r"\{.*\}", out.read_text(), re.S).group(0))["labels"]
        except Exception as e:  # noqa: BLE001
            return slug, f"파싱 실패 {e} {r.stderr[-300:]}"
        tids = type_ids(slug)
        with (J.folder(slug) / "labels" / f"calibration_{engine}.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["case_id", *tids, "메모"])
            for row in rows:
                lb = labels.get(row["case_id"], {})
                w.writerow([row["case_id"], *[str(lb.get(t, "NA")).upper() for t in tids], engine])
        return slug, "ok"

    with ThreadPoolExecutor(6 if engine == "codex" else 3) as ex:
        for slug, st in ex.map(run, jobs):
            print(slug, st)


# ── 일치도 ───────────────────────────────────────────────────────


def read_labels(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    out = {}
    for r in csv.DictReader(path.open(encoding="utf-8-sig")):
        out[r["case_id"]] = {k: (v or "NA").strip().upper() for k, v in r.items() if k.startswith("F-")}
    return out


def agree() -> None:
    """5-2: Codex 대 Grok 일치도. 갈린 칸은 사용자(우선)·Claude 가 판정한 최종 라벨과 함께 적는다."""
    import csv as _csv

    def src_map(path: Path) -> dict:
        if not path.exists():
            return {}
        return {
            (r["feature"].split("-")[0], r["case_id"], r["type"]): r["label"]
            for r in _csv.DictReader(path.open(encoding="utf-8-sig"))
        }

    lab = Path(__file__).parent / "runs" / "labels"
    user = {k: v for k, v in src_map(lab / "calibration_user_all.csv").items() if v in ("FAIL", "PASS")}
    for slug in S.D:
        if slug in EXCLUDED_SLUGS:
            continue
        d = J.folder(slug) / "labels"
        a, b = read_labels(d / "calibration_codex.csv"), read_labels(d / "calibration_grok.csv")
        gold = read_labels(d / "calibration_final.csv")
        if not a or not b:
            continue
        lines = ["| 실패 유형 | 둘 중 하나라도 판정 | 일치 | 일치율 |", "| --- | --- | --- | --- |"]
        dis = []
        for t in type_ids(slug):
            pairs = [(cid, a[cid].get(t, "NA"), b[cid].get(t, "NA")) for cid in a if cid in b]
            both = [(c, x, y) for c, x, y in pairs if x != "NA" or y != "NA"]
            same = sum(1 for _, x, y in both if x == y)
            rate = f"{same / len(both):.0%}" if both else "-"
            lines.append(f"| {t} | {len(both)} | {same} | {rate}{' ⚠️' if both and same / len(both) < 0.85 else ''} |")
            for c, x, y in both:
                if x != y:
                    who = "사용자" if (slug, c, t) in user else "Claude"
                    dis.append(f"- {c} {t}: Codex {x} / Grok {y} → 최종 {gold.get(c, {}).get(t, '?')} ({who})")
        write_section(
            slug,
            "5-2",
            "\n".join(
                [
                    "검토자: Codex + Grok (AI 2개 — 사용자 결정). 갈린 칸은 사용자가 판정하고, 사용자가 넘긴 칸은 Claude(Opus)가 판정.",
                    "주의: Claude 판정 칸은 Judge(Sonnet)와 같은 계열이라 5-3 일치도가 후하게 나올 수 있다.",
                    "",
                    *lines,
                    "",
                    "### 불일치 (최종 라벨)",
                    "",
                    *(dis or ["- 없음"]),
                ]
            ),
        )
        print(slug, "불일치", len(dis))


def calib_metric_status(
    tp: int,
    fn: int,
    tn: int,
    fp: int,
    unvalidated_count: int = 0,
    unvalidated_reasons: list[str] | None = None,
    is_quarantine: bool = False,
    has_provenance: bool = True,
    provenance_match: bool = True,
) -> tuple[float | None, float | None, str]:
    """한쪽 클래스 0, 표본 부재, 결측/오류, provenance 불일치/부재 시 통과로 왜곡되지 않도록 정직한 지표 및 상태 반환."""
    has_fail = (tp + fn) > 0
    has_pass = (tn + fp) > 0

    tpr = (tp / (tp + fn)) if has_fail else None
    tnr = (tn / (tn + fp)) if has_pass else None

    # 1. 참조 라벨 불일치 격리 (f09 F-01 등)
    if is_quarantine:
        return tpr, tnr, "⚠️ QUARANTINE_LABEL_CONFLICT"

    # 2. 표본 0 검증 (한쪽 클래스라도 0건이면 검증 미완료)
    if not has_fail and not has_pass:
        return None, None, "⚠️ 표본 0 (미검증)"
    if not has_fail:
        return None, tnr, "⚠️ FAIL 표본 0 (재현력 미검증)"
    if not has_pass:
        return tpr, None, "⚠️ PASS 표본 0 (오탐 미검증)"

    # 3. Provenance 검증 (과거 결과 / 불일치)
    if not has_provenance:
        return tpr, tnr, "⚠️ UNKNOWN/NOT_VALIDATED (역사적 관측치: 프롬프트 provenance 부재)"
    if not provenance_match:
        return tpr, tnr, "⚠️ UNKNOWN/NOT_VALIDATED (프롬프트 provenance 불일치)"

    # 4. 유효 정답 케이스 중 미채점/오류/누락 건수 존재 시
    if unvalidated_count > 0:
        reasons_str = ", ".join(unvalidated_reasons or ["미채점 표본 존재"])
        return tpr, tnr, f"⚠️ UNKNOWN/NOT_VALIDATED ({reasons_str})"

    # 5. 모든 정답 케이스가 완전 채점되고 provenance가 일치하며 양쪽 클래스 표본이 존재하는 경우
    if tpr >= 0.85 and tnr >= 0.85:
        return tpr, tnr, "✅ 통과"
    return tpr, tnr, "⚠️ 보정 필요"


def calib(write_eval_md: bool = False) -> dict[str, str]:
    """Judge 보정 지표 집계. 새 Sonnet 재판정 전까지 기존 EVAL.md 는 자동 덮어쓰지 않음."""
    reports = {}
    for slug in S.D:
        if slug in EXCLUDED_SLUGS:
            continue
        d = J.folder(slug) / "labels"
        gold = read_labels(d / "calibration_final.csv")
        res = latest_result(slug)
        if not gold or not res:
            continue

        res_rows = {r["case_id"]: r for r in res.get("rows", [])}
        prov = res.get("rejudge_provenance")

        lines = [
            "| 실패 유형 | 채점 | 정답 유효(필요) | 채점 가능 | 사람 FAIL | 사람 PASS | TPR (관측) | TNR (관측) | 판정 상태 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]

        unvalidated_details_by_tid: dict[str, list[tuple[str, str]]] = {}
        valid_counts: dict[str, int] = {}
        evaluable_counts: dict[str, int] = {}

        for t in S.D[slug]["types"]:
            tid = t["id"]
            how = "Judge" if tid in {x["id"] for x in J.judge_types(slug)} else "코드"
            is_judge = how == "Judge"
            # f09 F-01 의 DELETE+ADD 라벨 충돌은 2026-10-28 사용자 결정(4건 모두 PASS)으로 해소했다.
            is_quarantine = False

            # Provenance 확인
            if is_judge:
                if not prov:
                    has_prov = False
                    prov_match = False
                else:
                    curr_hash = compute_prompt_hash(slug, tid)
                    type_prov = prov.get("type_provenance", {}).get(tid, {})
                    rec_hash = type_prov.get("prompt_hash") or prov.get("prompt_hashes", {}).get(tid)
                    rec_status = type_prov.get("status")
                    if not rec_hash or rec_status == "historical_unmodified":
                        has_prov = False
                        prov_match = False
                    elif curr_hash and rec_hash != curr_hash:
                        has_prov = True
                        prov_match = False
                    else:
                        has_prov = True
                        prov_match = True
            else:
                has_prov = True
                prov_match = True

            # 골드셋에서 FAIL 또는 PASS로 라벨링된 유효 정답 케이스
            valid_cids = [cid for cid, lbs in gold.items() if lbs.get(tid) in ("FAIL", "PASS")]
            valid_counts[tid] = len(valid_cids)

            tp = fn = tn = fp = 0
            unvalidated_cases: list[tuple[str, str]] = []

            for cid in valid_cids:
                h = gold[cid][tid]
                r = res_rows.get(cid)

                if r is None:
                    unvalidated_cases.append((cid, "케이스 누락"))
                    continue

                s = r.get("sample", {})
                if s.get("error") or r.get("error"):
                    unvalidated_cases.append((cid, "실행 오류"))
                    continue

                trunc = False
                if r.get("truncation_warnings", {}).get(tid):
                    trunc = True
                elif s.get("truncated") or r.get("truncated"):
                    trunc = True
                elif r.get("judge_snapshots", {}).get(tid, {}).get("input_truncated") or r.get(
                    "judge_snapshots", {}
                ).get(tid, {}).get("output_truncated"):
                    trunc = True

                if trunc:
                    unvalidated_cases.append((cid, "입출력 잘림"))
                    continue

                v_map = r.get("verdict") or {}
                if tid not in v_map or v_map[tid] is None or v_map[tid] == "":
                    unvalidated_cases.append((cid, "판정 결측"))
                    continue

                m = str(v_map[tid]).strip().upper()
                judge_v = r.get("judge", {}).get(tid, {}).get("verdict")

                if m == "ERROR" or judge_v == "ERROR":
                    unvalidated_cases.append((cid, "기계 ERROR"))
                    continue

                if m == "NA":
                    unvalidated_cases.append((cid, "기계 NA"))
                    continue

                if m not in ("FAIL", "PASS"):
                    unvalidated_cases.append((cid, f"알 수 없는 판정({m})"))
                    continue

                # 정상 채점 케이스
                if h == "FAIL":
                    if m == "FAIL":
                        tp += 1
                    else:
                        fn += 1
                else:  # h == "PASS"
                    if m == "PASS":
                        tn += 1
                    else:
                        fp += 1

            unvalidated_details_by_tid[tid] = unvalidated_cases
            evaluable = tp + fn + tn + fp
            evaluable_counts[tid] = evaluable

            reason_counts = Counter(r for _, r in unvalidated_cases)
            reasons_summary = [f"{k} {v}건" for k, v in reason_counts.items()]

            tpr, tnr, status = calib_metric_status(
                tp=tp,
                fn=fn,
                tn=tn,
                fp=fp,
                unvalidated_count=len(unvalidated_cases),
                unvalidated_reasons=reasons_summary,
                is_quarantine=is_quarantine,
                has_provenance=has_prov,
                provenance_match=prov_match,
            )

            f_str = lambda v: "-" if v is None else f"{v:.2f}"  # noqa: E731
            lines.append(
                f"| {tid} | {how} | {len(valid_cids)} | {evaluable} | {tp + fn} | {tn + fp} | {f_str(tpr)} | {f_str(tnr)} | {status} |"
            )

        details = []
        for tid, unvals in unvalidated_details_by_tid.items():
            if unvals:
                items_str = ", ".join(f"{cid}({r})" for cid, r in unvals)
                details.append(
                    f"- **{tid}**: 필요 {valid_counts[tid]}건 중 채점 가능 {evaluable_counts[tid]}건, 누락/보류 {len(unvals)}건 — {items_str}"
                )
        if not details:
            details = ["- 누락 및 보류 항목 없음 (전수 채점 완료)"]

        report_text = "\n".join(
            [
                f"Judge 모델: Claude Sonnet (구독). 기준 실행: {res.get('version')} {res.get('run_at', '')[:16]}",
                "※ 한쪽 클래스 표본 0건, 판정 결측, 실행 오류, 잘림, 프롬프트 provenance 불일치/부재는 통과(✅)로 간주하지 않고 정직하게 표기함.",
                "",
                *lines,
                "",
                "### 누락 및 보류 상세",
                "",
                *details,
            ]
        )
        reports[slug] = report_text

        if write_eval_md:
            write_section(slug, "5-3", report_text)
            print(slug, "EVAL.md 5-3 표 작성 완료")
        else:
            print(f"[{slug}] 보정 지표 계산 완료 (EVAL.md 덮어쓰기 건너뜀)")
    return reports


def write_section(slug: str, sec: str, body: str) -> None:
    p = J.folder(slug) / "EVAL.md"
    t = p.read_text()
    title = {"5-1": "### 5-1. 채점기 구성", "5-2": "### 5-2. 불일치 목록", "5-3": "### 5-3. Judge 보정 결과"}[sec]
    nxt = {"5-1": "### 5-2.", "5-2": "### 5-3.", "5-3": "\n---"}[sec]
    i = t.index(title) + len(title)
    j = t.index(nxt, i)
    t = t[:i] + "\n\n" + body.strip() + "\n\n" + t[j:].lstrip("\n")
    p.write_text(t)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "sheet":
        sheet()
    elif cmd in ("codex", "grok"):
        codex(sys.argv[3] if len(sys.argv) > 3 and sys.argv[2] == "--only" else "", engine=cmd)
    elif cmd == "agree":
        agree()
    elif cmd == "calib":
        calib()
    else:
        print(__doc__)
