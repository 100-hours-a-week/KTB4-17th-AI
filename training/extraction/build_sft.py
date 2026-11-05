"""teacher 추출 라벨(scripts/label_with_teacher.py 의 extraction.jsonl)을 TRL 학습용 prompt/completion JSONL 로 바꾼다.

    uv run python training/extraction/build_sft.py data/teacher/qwen-fp8-sub1000/extraction.jsonl --allow-unreviewed

prompt 는 서빙 코드(RUBRIC, ExtractionAgent)를 그대로 import 해서 만든다.
RUBRIC 을 고치면 이미 학습한 어댑터와 입력이 달라지므로 manifest 의 prompt_sha256 으로 확인하고 다시 학습한다.
라벨의 extractPromptVersion 이 지금 RUBRIC 과 다르면 다시 라벨링해야 하므로 만들지 않는다.

옮기지 않는 것:
  - 골드셋(source=gold) 행과 호출·파싱·스키마 실패 행
  - 답하지 않은 차원에 점수를 낸 행. 학생이 그대로 배우면 환각이다 (F-05)
  - 스키마에 없는 최상위 키를 낸 행과 narrative 가 없는 행. 운영에서는 키를 버리지만 학생은 형식을 틀리게 배운다
  - 운영 사후 필터가 지우는 출력이 있는 행: 짐작 어미, 점수와 모순되는 서술, 근거 있는 점수가 없는 area 의 카드
  - 시드 성향과 정반대 구간(낮음↔높음)으로 점수를 낸 행. teacher 오류일 가능성이 높다
  - 평가 데이터셋과 같은 답이 있는 세션. 합성 생성기가 누수 세션을 버리는 규칙과 같다
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.features.persona.agents import RUBRIC, ExtractionAgent  # noqa: E402
from app.features.persona.schemas import SCORED, RawExtraction  # noqa: E402
from app.features.persona.service import _SPECULATION, narrative_contradiction, valid_summaries  # noqa: E402
from training.tagging.build_sft import is_validation  # noqa: E402
from training.tagging.data import normalize  # noqa: E402

GOLD_DATASETS = (
    PROJECT_ROOT / "evals/quality_datasets/persona_build.jsonl",
    PROJECT_ROOT / "evals/quality_datasets/claude_gold/persona_build.jsonl",
    PROJECT_ROOT / "evals/quality_datasets/human/persona_build.jsonl",
    PROJECT_ROOT / "evals/f03-persona-extraction/goldset.jsonl",
)
LOW_MAX = 35  # profile.py 의 서술 경계
HIGH_MIN = 65


def gold_answers(*paths: Path) -> set[str]:
    """평가 데이터셋의 사용자 답변. 학습에 섞이면 평가 점수가 부풀려진다."""
    answers: set[str] = set()
    for path in paths or GOLD_DATASETS:
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                data = json.loads(line)["input"]
                answers |= {normalize(m["content"]) for m in data.get("history", []) if m["role"] == "user"}
                answers |= {normalize(a) for a in data.get("answers", [])}
    return answers


def prompt_messages(row: dict) -> list[dict]:
    """운영 ExtractionAgent.extract 가 보내는 메시지와 같게 만든다."""
    history, answered = row["input"]["history"], set(row["input"]["answered"])
    content = ExtractionAgent._transcript(history) + ExtractionAgent._answered_section(answered)
    return [{"role": "system", "content": RUBRIC}, {"role": "user", "content": content}]


def to_record(row: dict) -> dict:
    return {
        "id": row["id"],
        "prompt": prompt_messages(row),
        "completion": [{"role": "assistant", "content": json.dumps(row["output"], ensure_ascii=False)}],
    }


def _opposite_to_seed(output: dict, seed_levels: dict) -> bool:
    for dim, level in seed_levels.items():
        score = output.get(dim)
        if not isinstance(score, int):
            continue
        if (level == "low" and score >= HIGH_MIN) or (level == "high" and score <= LOW_MAX):
            return True
    return False


def _narrative_text(output: dict) -> str:
    narrative = output.get("narrative") or {}
    parts = [narrative.get("headline", ""), narrative.get("body", ""), *narrative.get("traits", [])]
    for summary in output.get("summaries", []):
        parts += [summary.get("title", ""), summary.get("content", "")]
    return "\n".join(parts)


def skip_reason(row: dict) -> str:
    """이 행을 학습에 쓰면 안 되는 이유. 쓸 수 있으면 빈 문자열."""
    if row.get("source") != "synthetic":
        return "합성 데이터가 아님"
    output = row.get("output")
    if not output or row.get("error"):
        return "호출·파싱 실패"
    if row.get("schemaError"):
        return "스키마 위반"
    if row.get("unansweredKeys"):
        return "답하지 않은 차원에 점수"
    if set(output) - set(RawExtraction.model_fields):
        return "스키마에 없는 키"
    if not output.get("narrative"):
        return "narrative 없음"
    if _SPECULATION.search(_narrative_text(output)):
        return "짐작 어미"
    if _opposite_to_seed(output, (row.get("context") or {}).get("seedLevels") or {}):
        return "시드와 정반대 점수"
    # PersonaService.build_persona 의 후처리와 같은 판정. 운영이 버릴 출력을 학생이 배우면 결과 화면에서 서술·카드가 사라진다
    raw = RawExtraction.model_validate(output)
    answered = set(row["input"]["answered"])
    scores = {key: getattr(raw, key) if key in answered else None for key in SCORED}
    if narrative_contradiction(scores, raw.narrative):
        return "서술과 점수 모순"
    grounded = {SCORED[key].area for key, score in scores.items() if score is not None}
    if any(card["category"] not in grounded for card in valid_summaries(raw.summaries)):
        return "근거 없는 카드"
    return ""


def has_gold_answer(row: dict, gold: set[str]) -> bool:
    return any(m["role"] == "user" and normalize(m["content"]) in gold for m in row["input"]["history"])


def prompt_sha256() -> str:
    return hashlib.sha256(RUBRIC.encode()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="label_with_teacher.py 의 extraction.jsonl")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "training/extraction/out")
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--allow-unreviewed", action="store_true", help="사람 검수 전 teacher 라벨로 학습할 때")
    args = parser.parse_args(argv)

    if not args.allow_unreviewed:
        print("teacher 라벨은 사람 검수 전입니다. 그대로 학습하려면 --allow-unreviewed 를 붙이세요.", file=sys.stderr)
        return 1

    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    stale = sorted(
        {
            version
            for row in rows
            if (version := (row.get("teacher") or {}).get("extractPromptVersion")) and version != prompt_sha256()[:12]
        }
    )
    if stale:
        print(f"라벨의 프롬프트 버전 {stale} 이 지금 RUBRIC 과 다릅니다. 다시 라벨링하세요.", file=sys.stderr)
        return 1

    gold = gold_answers()
    kept: list[dict] = []
    skipped: Counter[str] = Counter()
    for row in rows:
        reason = skip_reason(row) or ("평가 데이터셋과 같은 답변" if has_gold_answer(row, gold) else "")
        if reason:
            skipped[reason] += 1
        else:
            kept.append(row)

    args.out.mkdir(parents=True, exist_ok=True)
    splits: dict[str, list[dict]] = {"train": [], "val": []}
    for row in kept:
        splits["val" if is_validation(row["familyId"], args.val_ratio) else "train"].append(to_record(row))
    for name, records in splits.items():
        with (args.out / f"{name}.jsonl").open("w", encoding="utf-8") as handle:
            handle.writelines(json.dumps(record, ensure_ascii=False) + "\n" for record in records)

    manifest = {
        "input": str(args.input),
        "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "prompt_sha256": prompt_sha256(),
        "counts": {name: len(records) for name, records in splits.items()},
        "skipped": dict(skipped),
        "sources": sorted({row["source"] for row in kept}),
        "unreviewed_allowed": args.allow_unreviewed,
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
