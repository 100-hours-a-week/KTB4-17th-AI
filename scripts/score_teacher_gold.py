"""골드셋 정답 대비 teacher 채점 — label_with_teacher.py --gold 출력 폴더 여러 개를 같은 척도로 비교한다.

척도는 docs/성능평가/10_증류_teacher_모델_선정기준.md 3절과 distillation_eval_plan 의 태깅·추출 척도를 따른다.

  태깅 (5회 샘플 다수결 기준)
    - primary micro 정밀도·재현율·F1, 정확 일치율 (primary 집합 + off_topic 허용값)
    - off_topic 오탐률(정답 false 인데 true) · 재현율(정답 true), 건수 함께
    - 유보율: 정답 primary 가 빈 정상 답변에서 primary 를 비운 비율
    - 일관된 오답: 5회 모두 같은 답인데 틀린 사례 수 — student 가 그대로 배우는 체계적 실수
    - 형식 위반: 파싱 실패·없는 차원·점수 필드 (샘플 기준)
    - 카테고리별 정확 일치
    - off_topic 해석이 갈려 사람 합의를 기다리는 4건(schema_comment_off_topic_conflict)은
      F2 기준서처럼 본 지표 분모에서 빼고 따로 센다
  추출 (원본 출력 기준, 서비스 사후 필터 전)
    - 점수형 차원 판정: 범위 안 정답 / 범위 밖 / 누락 / 정답 생략 / 환각 → 판정 정확도·환각률·누락률·범위 적중률
    - 텍스트형 유지·생략 판정 정확도
    - 서술 자동 검사: 짐작 없음·점수와 모순 없음·길이 계약·문장별 사용자 발화 인용

    .venv/bin/python scripts/score_teacher_gold.py data/teacher/gold-qwen data/teacher/gold-qwen-reasoning \\
        data/teacher/gold-flashlite-3.1 --out data/teacher/gold-comparison.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD_DIR = ROOT / "evals" / "quality_datasets"
HOLD_FLAG = "schema_comment_off_topic_conflict"


def _rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def load_gold(gold_dir: Path = GOLD_DIR) -> tuple[dict[str, dict], dict[str, dict]]:
    def rows(name: str) -> list[dict]:
        path = gold_dir / f"{name}.jsonl"
        return _rows(path) if path.exists() else []  # 사람 골드셋(human/)은 한쪽만 있을 수 있다

    tags = {f"gold:{r['metadata']['caseId']}": r for r in rows("persona_onboarding_tagging")}
    builds = {f"gold:{r['metadata']['caseId']}": r for r in rows("persona_build")}
    return tags, builds


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


# ══ 태깅 ══════════════════════════════════════════════════


def score_tagging(rows: list[dict], gold: dict[str, dict]) -> dict:
    tp = fp = fn = 0
    exact = total = 0
    off_fp = off_neg = off_tp = off_pos = 0
    abstain = abstain_den = 0
    consistent_wrong: list[str] = []
    by_category: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    held = {"items": 0, "exact": 0}
    samples = parse_fail = invalid = extra = 0
    agreements: list[float] = []
    per_item: dict[str, dict] = {}

    for row in rows:
        g = gold.get(row["id"])
        if g is None:
            continue
        for s in row["samples"]:
            samples += 1
            if "error" in s:
                parse_fail += 1
                continue
            invalid += bool(s["invalidDimensions"])
            extra += bool(s["extraKeys"])
        agg = row["aggregate"]
        metrics = g["expectedOutput"]["autoMetrics"]
        gold_primary = set(metrics["primary"]["accepted"][0])
        off_accepted = metrics["off_topic"]["accepted"]
        maj = agg.get("majority") or {"primary": [], "off_topic": None}
        pred = set(maj["primary"])
        ok = pred == gold_primary and maj["off_topic"] in off_accepted
        per_item[row["id"]] = {"correct": ok, "pred": sorted(pred), "predOffTopic": maj["off_topic"]}

        if HOLD_FLAG in g["metadata"]["reviewFlags"]:
            held["items"] += 1
            held["exact"] += ok
            continue

        total += 1
        exact += ok
        tp += len(pred & gold_primary)
        fp += len(pred - gold_primary)
        fn += len(gold_primary - pred)
        if off_accepted == [False]:
            off_neg += 1
            off_fp += maj["off_topic"] is True
            if not gold_primary:
                abstain_den += 1
                abstain += not pred and maj["off_topic"] is False
        elif off_accepted == [True]:
            off_pos += 1
            off_tp += maj["off_topic"] is True
        if agg.get("validSamples"):
            agreements.append(agg["agreement"])
            if not ok and agg["agreement"] == 1.0:
                consistent_wrong.append(row["id"])
        cat = by_category[g["metadata"]["category"]]
        cat[0] += ok
        cat[1] += 1

    precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    f1 = round(2 * precision * recall / (precision + recall), 4) if precision and recall else 0.0
    return {
        "items": total,
        "primaryPrecision": precision,
        "primaryRecall": recall,
        "primaryF1": f1,
        "exactMatch": _ratio(exact, total),
        "offTopicFalsePositiveRate": _ratio(off_fp, off_neg),
        "offTopicFalsePositives": f"{off_fp}/{off_neg}",
        "offTopicRecall": _ratio(off_tp, off_pos),
        "offTopicHits": f"{off_tp}/{off_pos}",
        "abstentionRate": _ratio(abstain, abstain_den),
        "abstentions": f"{abstain}/{abstain_den}",
        "meanAgreement": round(sum(agreements) / len(agreements), 4) if agreements else None,
        "consistentWrong": len(consistent_wrong),
        "consistentWrongIds": consistent_wrong,
        "formatViolations": {
            "samples": samples,
            "parseOrCallFail": parse_fail,
            "invalidDims": invalid,
            "extraKeys": extra,
        },
        "byCategory": {k: f"{v[0]}/{v[1]}" for k, v in sorted(by_category.items())},
        "heldForReview": held,
        "perItem": per_item,
    }


# ══ 추출 ══════════════════════════════════════════════════

_SENT = re.compile(r"(?<=[.!?])\s+")


def _norm(text: str) -> str:
    return re.sub(r"[^\w가-힣]", "", unicodedata.normalize("NFKC", text))


def grounded(sentence: str, user_text: str, n: int = 5) -> bool:
    """서술 문장이 사용자 발화와 n글자 이상 이어진 구간을 공유하는가 — "문장마다 사용자가 한 말을 옮겨 쓴다" 검사."""
    s, u = _norm(sentence), _norm(user_text)
    return any(s[i : i + n] in u for i in range(len(s) - n + 1))


def narrative_checks(raw: dict, history: list[dict], answered: set[str]) -> dict | None:
    from pydantic import ValidationError

    from app.features.persona.schemas import SCORED, Narrative
    from app.features.persona.service import narrative_contradiction, without_speculation

    if not raw.get("narrative"):
        return None
    try:
        narrative = Narrative.model_validate(raw["narrative"])
    except ValidationError:
        return {"valid": False}
    kept = without_speculation(narrative)
    sentences = [x for x in _SENT.split(narrative.body.strip()) if x]
    scores = {k: raw.get(k) for k in SCORED if k in answered and isinstance(raw.get(k), int)}
    user_text = " ".join(m["content"] for m in history if m["role"] == "user")
    checks = {
        "noSpeculation": kept is not None and kept.body == narrative.body.strip() and kept.traits == narrative.traits,
        "noContradiction": narrative_contradiction(scores, narrative) is None,
        "lengthContract": (
            len(narrative.headline) <= 40
            and 1 <= len(sentences) <= 5
            and 3 <= len(narrative.traits) <= 5
            and all(len(t) <= 30 for t in narrative.traits)
        ),
        "quoteGrounded": bool(sentences) and all(grounded(x, user_text) for x in sentences),
    }
    return {"valid": True, **checks}


def score_extraction(rows: list[dict], gold: dict[str, dict]) -> dict:
    from app.features.persona.schemas import SCORED

    c = Counter()  # tn fp fn tp tp_out
    text_ok = text_total = 0
    failed = schema_err = 0
    narr: list[dict] = []
    narrative_missing = 0
    items = 0
    per_item: dict[str, dict] = {}

    for row in rows:
        g = gold.get(row["id"])
        if g is None:
            continue
        items += 1
        if "output" not in row:
            failed += 1
            per_item[row["id"]] = {"failed": True}
            continue
        schema_err += bool(row.get("schemaError"))
        raw = row["output"]
        m = g["expectedOutput"]["autoMetrics"]
        ranges, must_omit = m["scoreInRange"], set(m["mustOmitFromRawModel"])
        item = Counter()
        for dim in SCORED:
            value = raw.get(dim)
            present = isinstance(value, int) and not isinstance(value, bool)
            if dim in ranges:
                low, high = ranges[dim]
                item["tp" if present and low <= value <= high else "tp_out" if present else "fn"] += 1
            elif dim in must_omit:
                item["fp" if present else "tn"] += 1
        c.update(item)
        for dim in m["textualKept"]:
            text_total += 1
            text_ok += bool(raw.get(dim))
        for dim in m["textualDropped"]:
            text_total += 1
            text_ok += not raw.get(dim)
        checks = narrative_checks(raw, row["input"]["history"], set(row["input"]["answered"]))
        if checks is None:
            narrative_missing += 1
        elif checks.get("valid"):
            narr.append(checks)
        per_item[row["id"]] = {
            "decisions": dict(item),
            "scores": {d: raw.get(d) for d in ranges},
            "goldRanges": ranges,
            "narrative": checks,
        }

    decisions = sum(c.values())
    check_names = ["noSpeculation", "noContradiction", "lengthContract", "quoteGrounded"]
    narrative_rates = {k: _ratio(sum(n[k] for n in narr), len(narr)) for k in check_names}
    passed = [sum(n[k] for k in check_names) / len(check_names) for n in narr]
    return {
        "items": items,
        "callOrParseFail": failed,
        "schemaErrors": schema_err,
        "dimensionDecisionAccuracy": _ratio(c["tn"] + c["tp"], decisions),
        "hallucinationRate": _ratio(c["fp"], c["fp"] + c["tn"]),
        "missRate": _ratio(c["fn"], c["fn"] + c["tp"] + c["tp_out"]),
        "inRangeRate": _ratio(c["tp"], c["tp"] + c["tp_out"]),
        "inRange": f"{c['tp']}/{c['tp'] + c['tp_out'] + c['fn']}",
        "decisions": dict(c),
        "textualDecisionAccuracy": _ratio(text_ok, text_total),
        "narrativeMissing": narrative_missing,
        "narrativeAutoScore": round(sum(passed) / len(passed), 4) if passed else None,
        "narrativeChecks": narrative_rates,
        "perItem": per_item,
    }


# ══ 비교 ══════════════════════════════════════════════════


def score_run(run_dir: Path, gold_tags: dict, gold_builds: dict) -> dict:
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    return {
        "run": run_dir.name,
        "model": summary.get("teacher", {}).get("model"),
        "reasoning": summary.get("teacher", {}).get("reasoning"),
        "costUsd": summary.get("estimatedCostUsd"),
        "elapsedSeconds": summary.get("elapsedSeconds"),
        "tagging": score_tagging(_rows(run_dir / "tagging.jsonl"), gold_tags),
        "extraction": score_extraction(_rows(run_dir / "extraction.jsonl"), gold_builds),
    }


TABLE = [
    ("태깅 primary F1", "tagging", "primaryF1"),
    ("태깅 primary 정밀도", "tagging", "primaryPrecision"),
    ("태깅 primary 재현율", "tagging", "primaryRecall"),
    ("태깅 정확 일치율", "tagging", "exactMatch"),
    ("off_topic 오탐", "tagging", "offTopicFalsePositives"),
    ("off_topic 재현", "tagging", "offTopicHits"),
    ("빈 primary 유보", "tagging", "abstentions"),
    ("판정 일치율 (5회)", "tagging", "meanAgreement"),
    ("일관된 오답 수", "tagging", "consistentWrong"),
    ("추출 차원 판정 정확도", "extraction", "dimensionDecisionAccuracy"),
    ("추출 환각률", "extraction", "hallucinationRate"),
    ("추출 누락률", "extraction", "missRate"),
    ("추출 범위 적중", "extraction", "inRange"),
    ("텍스트형 판정 정확도", "extraction", "textualDecisionAccuracy"),
    ("서술 자동 점수", "extraction", "narrativeAutoScore"),
    ("추출 호출·파싱 실패", "extraction", "callOrParseFail"),
]


def markdown_table(results: list[dict]) -> str:
    head = "| 지표 | " + " | ".join(r["run"] for r in results) + " |"
    sep = "|---|" + "---|" * len(results)
    lines = [head, sep]
    for label, section, key in TABLE:
        lines.append(f"| {label} | " + " | ".join(str(r[section].get(key)) for r in results) + " |")
    fmt = [r["tagging"]["formatViolations"] for r in results]
    lines.append(
        "| 태깅 형식 위반 (파싱·없는 차원·점수 키) | "
        + " | ".join(f"{f['parseOrCallFail']}·{f['invalidDims']}·{f['extraKeys']} / {f['samples']}" for f in fmt)
        + " |"
    )
    lines.append("| 비용 (USD) | " + " | ".join(str(r["costUsd"]) for r in results) + " |")
    lines.append("| 소요 (초) | " + " | ".join(str(r["elapsedSeconds"]) for r in results) + " |")
    return "\n".join(lines)


def disagreements(results: list[dict], gold_tags: dict) -> list[dict]:
    """같은 문항에서 맞고 틀림이 갈린 태깅 사례 — 평균보다 이걸 읽어야 어느 쪽 실수가 체계적인지 보인다."""
    ids = sorted(set.intersection(*(set(r["tagging"]["perItem"]) for r in results)))
    out = []
    for i in ids:
        verdicts = {r["run"]: r["tagging"]["perItem"][i] for r in results}
        if len({v["correct"] for v in verdicts.values()}) > 1:
            g = gold_tags[i]
            out.append(
                {
                    "id": i,
                    "category": g["metadata"]["category"],
                    "question": g["input"]["question"],
                    "answer": g["input"]["answer"],
                    "gold": g["expectedOutput"]["autoMetrics"]["primary"]["accepted"][0],
                    "runs": {k: ("✓ " if v["correct"] else "✗ ") + str(v["pred"]) for k, v in verdicts.items()},
                }
            )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("runs", nargs="+", type=Path, help="label_with_teacher.py --gold 출력 폴더들")
    parser.add_argument("--out", type=Path, default=None, help="전체 결과 JSON 경로")
    parser.add_argument(
        "--gold-dir", type=Path, default=GOLD_DIR, help="골드셋 폴더. 사람 골드셋은 evals/quality_datasets/human"
    )
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))

    gold_tags, gold_builds = load_gold(args.gold_dir)
    results = [score_run(r, gold_tags, gold_builds) for r in args.runs]
    diffs = disagreements(results, gold_tags) if len(results) > 1 else []
    print(markdown_table(results))
    print(f"\n판정이 갈린 태깅 사례: {len(diffs)}건")
    for d in diffs:
        print(f"- [{d['category']}] {d['answer']} (정답 {d['gold']})")
        for run, verdict in d["runs"].items():
            print(f"    {run}: {verdict}")
    if args.out:
        args.out.write_text(
            json.dumps({"results": results, "disagreements": diffs}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
