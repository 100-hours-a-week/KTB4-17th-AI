"""Judge 판정만 다시 — results 의 최신 실행에서 ERROR(구독 한도 등) 인 판정을 다시 부른다. 앱은 돌리지 않는다.

python3 stage5_rejudge.py [--split calibration] [--only f01,f03] [--workers 3]
python3 stage5_rejudge.py --types f01:F-01,f03:F-02   # 지정한 유형은 ERROR 가 아니어도 전부 다시 판정 (Judge 보정용)
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import stage3_types as S  # noqa: E402
import stage5_judges as J  # noqa: E402
from stage5_labels import goldset  # noqa: E402


def verdicts(slug, codes, judges):
    v = {}
    for t in S.D[slug]["types"]:
        tid = t["id"]
        if tid in judges:
            v[tid] = judges[tid]["verdict"]
        elif tid in codes and codes[tid]["pass"] is not None:
            v[tid] = "PASS" if codes[tid]["pass"] else "FAIL"
        else:
            v[tid] = "NA"
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="calibration")
    ap.add_argument("--only", default="")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--types", default="", help="slug:F-xx,... — 이 유형은 모든 문제를 다시 판정")
    a = ap.parse_args()
    force: dict[str, set] = {}
    for x in filter(None, a.types.split(",")):
        sl, t = x.split(":")
        force.setdefault(sl, set()).add(t)
    if force and not a.only:
        a.only = ",".join(force)
    pool = ThreadPoolExecutor(a.workers)
    for slug in S.D:
        if a.only and slug not in a.only.split(","):
            continue
        ps = sorted(p for p in (J.folder(slug) / "results").glob(f"*_{a.split}.json") if "smoke" not in p.name)
        if not ps:
            continue
        p = ps[-1]
        d = json.loads(p.read_text())
        gs = goldset(slug)
        todo = 0
        for r in d["rows"]:
            bad = [k for k, v in r["judge"].items() if v["verdict"] == "ERROR" or k in force.get(slug, ())]
            if not bad:
                continue
            c = gs[r["case_id"]]
            s = r["sample"]
            inp, out, orc = J.view_input(slug, c["input"], s), J.view_output(slug, s), J.oracle_text(c)
            futs = {
                k: pool.submit(
                    J.call,
                    J.fill(
                        (J.folder(slug) / "judges" / f"{k}.md").read_text(), J.cut(inp, 6000), J.cut(out, 6000), orc
                    ),
                )
                for k in bad
            }
            for k, f in futs.items():
                r["judge"][k] = f.result()
            r["verdict"] = verdicts(slug, r["code"], r["judge"])
            todo += len(bad)
        p.write_text(json.dumps(d, ensure_ascii=False, indent=1))
        left = sum(1 for r in d["rows"] for v in r["judge"].values() if v["verdict"] == "ERROR")
        print(slug, "다시 판정", todo, "남은 ERROR", left, flush=True)
        if J.LIMIT_HIT.is_set():
            print("구독 한도 — 멈춤. 한도가 풀리면 다시 실행하세요.")
            break


if __name__ == "__main__":
    main()
