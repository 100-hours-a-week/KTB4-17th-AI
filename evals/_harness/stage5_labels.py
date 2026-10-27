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
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import stage3_types as S  # noqa: E402
import stage5_judges as J  # noqa: E402

EVALS = Path(__file__).resolve().parents[1]


def latest_result(slug: str, split: str = "calibration") -> dict | None:
    ps = sorted((J.folder(slug) / "results").glob(f"*_{split}.json"))
    ps = [p for p in ps if "smoke" not in p.name]
    return json.loads(ps[-1].read_text()) if ps else None


def goldset(slug: str) -> dict[str, dict]:
    return {
        c["id"]: c for c in (json.loads(x) for x in (J.folder(slug) / "goldset.jsonl").read_text().splitlines() if x)
    }


def type_ids(slug: str) -> list[str]:
    return [t["id"] for t in S.D[slug]["types"]]


# ── 시트 ─────────────────────────────────────────────────────────


def sheet() -> None:
    for slug in S.D:
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


def calib() -> None:
    for slug in S.D:
        d = J.folder(slug) / "labels"
        gold = read_labels(d / "calibration_final.csv")
        res = latest_result(slug)
        if not gold or not res:
            continue
        lines = [
            "| 실패 유형 | 채점 | 사람 FAIL | 사람 PASS | TPR | TNR | 판정 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for t in S.D[slug]["types"]:
            tid = t["id"]
            tp = fn = tn = fp = 0
            for r in res["rows"]:
                h = gold.get(r["case_id"], {}).get(tid, "NA")
                m = r["verdict"].get(tid, "NA")
                if h == "NA" or m in ("NA", "ERROR"):
                    continue
                if h == "FAIL":
                    tp += m == "FAIL"
                    fn += m != "FAIL"
                else:
                    tn += m != "FAIL"
                    fp += m == "FAIL"
            tpr = tp / (tp + fn) if tp + fn else None
            tnr = tn / (tn + fp) if tn + fp else None
            ok = (tpr is None or tpr >= 0.85) and (tnr is None or tnr >= 0.85)
            how = "Judge" if tid in {x["id"] for x in J.judge_types(slug)} else "코드"
            f = lambda v: "-" if v is None else f"{v:.2f}"  # noqa: E731
            lines.append(
                f"| {tid} | {how} | {tp + fn} | {tn + fp} | {f(tpr)} | {f(tnr)} | {'✅' if ok else '⚠️ 보정 필요'} |"
            )
        write_section(
            slug,
            "5-3",
            "\n".join(
                [f"Judge 모델: Claude Sonnet (구독). 기준 실행: {res['version']} {res['run_at'][:16]}", "", *lines]
            ),
        )
        print(slug, "보정 표 작성")


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
