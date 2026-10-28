"""저장된 출력으로 코드 검사만 다시 채점한다 (모델 호출 없음). 검사 코드를 고친 뒤 쓴다.

python stage5_rescore.py [--split calibration] [--only f04,f05]
"""

import argparse
import glob
import json
from pathlib import Path

import stage5_labels as L
import stage5_run as RUN


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="calibration", choices=["calibration", "regression"])
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    only = set(filter(None, a.only.split(",")))
    for slug in RUN.S.D:
        if only and slug not in only:
            continue
        files = sorted(
            x for x in glob.glob(str(L.J.folder(slug) / "results" / f"*_{a.split}.json")) if "smoke" not in x
        )
        if not files:
            continue
        p = Path(files[-1])
        res = json.loads(p.read_text())
        gold = L.goldset(slug)
        changed = 0
        for r in res["rows"]:
            case = gold[r["case_id"]]
            codes = RUN.code_score(slug, case, {"id": r["case_id"], "input": case["input"], **r["sample"]})
            judges = {k: v for k, v in (r.get("judge") or {}).items()}
            v = RUN.verdicts(slug, codes, judges)
            changed += sum(1 for k in v if v[k] != r["verdict"].get(k))
            r["code"], r["verdict"] = codes, v
        p.write_text(json.dumps(res, ensure_ascii=False, indent=1))
        print(slug, p.name, "바뀐 판정", changed)


if __name__ == "__main__":
    main()
