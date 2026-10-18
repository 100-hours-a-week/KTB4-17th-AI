"""합성 세션 teacher 라벨 점검 — 정답이 없는 합성 데이터를 시드 프로필(잠재 성향)과 생성 의도로 검사한다.

골드셋 채점(score_teacher_gold.py)과 달리 여기 기준은 "약한 정답"이다. 시뮬레이터가 시드를 완벽히 연기하지
않으므로 불일치가 곧 teacher 오류는 아니다. 그래서 수치보다 불일치 사례를 골라 학습 데이터에서 뺄지 정하는 데 쓴다.

  태깅
    - 의도한 무관 답변(reask 전 답)을 off_topic 으로 잡은 비율
    - 답변 방식(normal·short·joke)별 off_topic 비율, primary 가 빈 비율
    - 판정 일치율 분포 — 낮은 항목은 소프트 라벨로만 쓰거나 뺀다
  추출 (시드 대비)
    - 구간 일치율: teacher 점수의 낮음(<35)·중간·높음(>64)이 시드 수준과 같은 비율
    - 시드 점수와의 평균 절대 오차
    - 시드에서 "잘 모름"인 차원에 teacher 가 점수를 낸 비율
    - 시드 수준(낮음·중간·높음) → teacher 구간 혼동표
  답변
    - 행동 예시 복붙 비율: 시뮬레이터 v2 의 DIMENSION_CUES 문장과 8자 이상 이어서 같은 답변 비율
      (높으면 세션끼리 답이 비슷해지고 student 가 문장을 외운다)
    - 평균 길이

    .venv/bin/python scripts/check_synthetic_labels.py data/teacher/pilot200 --sessions data/synthetic/pilot200.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOW_AGREEMENT = 0.6


def _rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def bucket(score: int) -> str:
    # 시드 구간(LEVEL_RANGES)과 profile.py 서술 경계 35/65 에 맞춘다
    return "low" if score < 35 else "high" if score >= 65 else "mid"


def _ratio(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def check_tagging(rows: list[dict]) -> dict:
    by_mode: dict[str, Counter] = defaultdict(Counter)
    agreements = []
    low_agreement: list[str] = []
    for r in rows:
        agg = r["aggregate"]
        if not agg.get("validSamples"):
            by_mode["(failed)"]["items"] += 1
            continue
        mode = "intended_off_topic" if r["context"].get("intendedOffTopic") else r["context"].get("mode", "?")
        m = by_mode[mode]
        m["items"] += 1
        m["offTopic"] += agg["majority"]["off_topic"]
        m["emptyPrimary"] += not agg["majority"]["primary"] and not agg["majority"]["off_topic"]
        agreements.append(agg["agreement"])
        if agg["agreement"] < LOW_AGREEMENT:
            low_agreement.append(r["id"])
    return {
        "items": len(rows),
        "byMode": {
            k: {
                "items": v["items"],
                "offTopicRate": _ratio(v["offTopic"], v["items"]),
                "emptyPrimaryRate": _ratio(v["emptyPrimary"], v["items"]),
            }
            for k, v in sorted(by_mode.items())
        },
        "meanAgreement": round(sum(agreements) / len(agreements), 4) if agreements else None,
        "lowAgreementItems": len(low_agreement),
        "lowAgreementIds": low_agreement,
    }


def _leak_source(dim: str, extraction_id: str, tags: dict[str, dict], topics: list[str]) -> str:
    """ "잘 모름" 차원이 어느 턴에서 근거로 잡혔나. direct = 그 차원을 겨눈 질문에서 답해 버림(시뮬레이터 실수),
    cross = 다른 질문에 답하다 드러남(실제 사람도 그런다). topics 는 턴 순서대로의 주제 id."""
    from app.features.persona.schemas import TOPICS_BY_ID

    for i, topic_id in enumerate(topics):
        row = tags.get(f"{extraction_id}:t{i}")
        if row and dim in (row["aggregate"].get("majority") or {}).get("primary", []):
            topic = TOPICS_BY_ID[topic_id]
            if dim in (*topic.covers, *topic.also_touches):
                return "direct"
    return "cross"


def check_extraction(
    rows: list[dict],
    seeds: dict[str, dict],
    *,
    tags: dict[str, dict] | None = None,
    topics: dict[str, list[str]] | None = None,
) -> dict:
    from app.features.persona.schemas import SCORED

    same = total = 0
    abs_err: list[int] = []
    unknown_scored = unknown_total = 0
    leak_source: Counter = Counter()
    per_dim: dict[str, Counter] = defaultdict(Counter)
    confusion: dict[str, Counter] = defaultdict(Counter)
    failed = 0
    mismatches: list[dict] = []
    for r in rows:
        if "output" not in r:
            failed += 1
            continue
        seed = seeds.get(r["id"].removeprefix("synthetic:"))
        if seed is None:
            continue
        out = r["output"]
        for dim in SCORED:
            level = seed["levels"][dim]
            value = out.get(dim)
            has = isinstance(value, int) and not isinstance(value, bool)
            if level == "unanswered":
                unknown_total += 1
                unknown_scored += has
                if has and tags is not None and topics is not None:
                    sid = r["id"].removeprefix("synthetic:")
                    leak_source[_leak_source(dim, r["id"], tags, topics.get(sid, []))] += 1
                continue
            if not has:
                continue
            total += 1
            confusion[level][bucket(value)] += 1
            ok = bucket(value) == level
            same += ok
            abs_err.append(abs(value - seed["scores"][dim]))
            per_dim[dim]["n"] += 1
            per_dim[dim]["same"] += ok
            if not ok and abs(value - seed["scores"][dim]) >= 40:
                mismatches.append({"id": r["id"], "dim": dim, "seed": level, "teacher": value})
    return {
        "items": len(rows),
        "failed": failed,
        "scoredDecisions": total,
        "bucketAgreement": _ratio(same, total),
        "meanAbsError": round(sum(abs_err) / len(abs_err), 1) if abs_err else None,
        "unknownDimScoredRate": _ratio(unknown_scored, unknown_total),
        # "잘 모름" 차원을 겨눈 질문에서 답해 버린 비율 — 시뮬레이터 충실도의 직접 지표
        "unknownDirectLeakRate": _ratio(leak_source["direct"], unknown_total) if tags is not None else None,
        "unknownCrossLeakRate": _ratio(leak_source["cross"], unknown_total) if tags is not None else None,
        "bucketAgreementByDim": {d: _ratio(c["same"], c["n"]) for d, c in sorted(per_dim.items())},
        "confusion": {lv: dict(confusion[lv]) for lv in ("low", "mid", "high")},
        "largeMismatches": mismatches,
    }


def _norm(text: str) -> str:
    import re

    return re.sub(r"[^\w가-힣]", "", text)


def check_answers(sessions: list[dict], n: int = 8) -> dict:
    from scripts.generate_synthetic_onboarding import DIMENSION_CUES

    cues = [_norm(c) for levels in DIMENSION_CUES.values() for group in levels.values() for c in group]
    grams = {c[i : i + n] for c in cues for i in range(len(c) - n + 1)}
    answers = [t["answer"] for s in sessions for t in s["turns"]]
    copied = sum(any(a[i : i + n] in grams for i in range(len(a) - n + 1)) for a in map(_norm, answers))
    return {
        "answers": len(answers),
        "cueCopyRate": _ratio(copied, len(answers)),
        "meanLength": round(sum(map(len, answers)) / len(answers), 1) if answers else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("run", type=Path, help="label_with_teacher.py --synthetic 출력 폴더")
    parser.add_argument("--sessions", type=Path, required=True, help="generate_synthetic_onboarding.py 출력 JSONL")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))

    sessions = _rows(args.sessions)
    seeds = {s["sessionId"]: s["seedProfile"] for s in sessions}
    tag_rows = [r for r in _rows(args.run / "tagging.jsonl") if r["source"] == "synthetic"]
    topics = {s["sessionId"]: [t["topicId"] for t in s["turns"]] for s in sessions}
    result = {
        "tagging": check_tagging(tag_rows),
        "extraction": check_extraction(
            [r for r in _rows(args.run / "extraction.jsonl") if r["source"] == "synthetic"],
            seeds,
            tags={r["id"]: r for r in tag_rows},
            topics=topics,
        ),
        "answers": check_answers(sessions),
    }
    brief = json.loads(json.dumps(result))
    brief["tagging"].pop("lowAgreementIds")
    brief["extraction"]["largeMismatches"] = len(result["extraction"]["largeMismatches"])
    print(json.dumps(brief, ensure_ascii=False, indent=2))
    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
