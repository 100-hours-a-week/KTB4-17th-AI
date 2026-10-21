"""teacher 라벨(scripts/label_with_teacher.py 출력) → 학습 데이터 형식(TagExample JSONL).

    uv run python training/tagging/from_teacher.py data/teacher/pilot200/tagging.jsonl --out data/tagging.jsonl
    uv run python training/tagging/build_sft.py data/tagging.jsonl --allow-unreviewed   # 검수 전 파이프라인 시험

teacher 의 5회 샘플 다수결을 정답 후보로 옮긴다. reviewed_by 는 비워 둔다 — 모델이 만든 라벨이라 사람이 확인하기
전에는 build_sft.py 가 학습을 막는다(--allow-unreviewed 는 파이프라인 시험용). 검수할 때는 agreement 가 낮은 것부터 본다.

옮기지 않는 것:
  - 골드셋(source=gold) 행 — 평가 데이터셋이라 학습에 넣으면 누수다
  - 다수결이 없는 행(샘플 전부 실패)과 판정 일치율이 --min-agreement 미만인 행
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from training.tagging.data import TagExample  # noqa: E402


def convert(row: dict, *, min_agreement: float) -> tuple[TagExample | None, str]:
    """teacher 태깅 한 행 → (TagExample, 건너뛴 이유). 옮기면 이유는 빈 문자열."""
    if row.get("source") != "synthetic":
        return None, "합성 데이터가 아님"
    agg = row.get("aggregate") or {}
    if not agg.get("validSamples"):
        return None, "다수결 없음"
    if agg["agreement"] < min_agreement:
        return None, "판정 일치율 낮음"
    maj = agg["majority"]
    off = bool(maj["off_topic"])
    primary = [] if off else sorted(set(maj["primary"]))
    # 같은 차원이 primary 와 secondary 에 동시에 다수결을 얻으면 primary 로만 둔다 (data.validate 규칙)
    secondary = [] if off else sorted(set(maj["secondary"]) - set(primary))
    example = TagExample(
        id=row["id"],
        question=row["input"]["question"],
        answer=row["input"]["answer"],
        primary=primary,
        secondary=secondary,
        off_topic=off,
        source="synthetic",
    )
    return example, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="label_with_teacher.py 의 tagging.jsonl")
    parser.add_argument("--out", type=Path, required=True, help="TagExample JSONL")
    parser.add_argument("--min-agreement", type=float, default=0.6, help="이보다 일치율이 낮은 행은 뺀다")
    args = parser.parse_args(argv)

    kept: list[TagExample] = []
    skipped: Counter[str] = Counter()
    for line in args.input.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        example, reason = convert(json.loads(line), min_agreement=args.min_agreement)
        if example is None:
            skipped[reason] += 1
        else:
            kept.append(example)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(ex.model_dump_json() + "\n" for ex in kept), encoding="utf-8")
    print(json.dumps({"written": len(kept), "skipped": dict(skipped), "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
