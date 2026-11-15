"""평가 게이트 — 후보 어댑터가 champion 보다 나빠지지 않았는지 (설계 5.7).

  python training/pipeline/gate.py --gold-champion g_c.json --gold-candidate g_n.json \\
      --crowd-champion c_c.json --crowd-candidate c_n.json --out gate.json

입력은 scripts/eval_tagging.py(사람 골드셋) · scripts/eval_crowd_tagging.py(다수결 평가셋) 결과 파일.
모든 조건을 통과해야 passed. 통과해도 운영 교체는 사람이 승인한다 (후보 태그로만 올린다).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_FAILURE_PCT = 1.0  # JSON 실패는 champion 이 이보다 높아도 이 선까지는 허용


@dataclass(frozen=True)
class Check:
    name: str
    champion: float
    candidate: float
    passed: bool


def checks(gold_champion: dict, gold_candidate: dict, crowd_champion: dict, crowd_candidate: dict) -> list[Check]:
    def at_least(name: str, c: float, n: float) -> Check:
        return Check(name, c, n, n >= c)

    def at_most(name: str, c: float, n: float, floor: float = 0.0) -> Check:
        return Check(name, c, n, n <= max(c, floor))

    g_c, g_n = gold_champion["summary"], gold_candidate["summary"]
    c_c, c_n = crowd_champion["summary"], crowd_candidate["summary"]
    return [
        at_least("gold_exact_pct", g_c["exact_pct"], g_n["exact_pct"]),
        at_least("gold_primary_pct", g_c["primary_pct"], g_n["primary_pct"]),
        at_most("gold_failure_pct", g_c["failure_pct"], g_n["failure_pct"], MAX_FAILURE_PCT),
        at_least("crowd_accuracy_pct", c_c["accuracy_pct"], c_n["accuracy_pct"]),
        # AMBIGUOUS 문장을 단정하는 비율은 늘면 안 된다
        at_most("crowd_ambiguous_confident_pct", c_c["ambiguous_confident_pct"], c_n["ambiguous_confident_pct"]),
        at_most("crowd_failure_pct", c_c["failure_pct"], c_n["failure_pct"], MAX_FAILURE_PCT),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("gold-champion", "gold-candidate", "crowd-champion", "crowd-candidate"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    loaded = [json.loads(p.read_text(encoding="utf-8")) for p in (args.gold_champion, args.gold_candidate)]
    loaded += [json.loads(p.read_text(encoding="utf-8")) for p in (args.crowd_champion, args.crowd_candidate)]
    result = checks(*loaded)
    report = {"passed": all(c.passed for c in result), "checks": [asdict(c) for c in result]}
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 3


if __name__ == "__main__":
    sys.exit(main())
