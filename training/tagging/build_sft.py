"""검증한 태깅 예시를 TRL 학습용 prompt/completion JSONL 로 바꾼다.

    uv run python training/tagging/build_sft.py data/tagging.jsonl --out training/tagging/out

prompt 는 서빙 코드(TAG_PROMPT, tagging_messages)를 그대로 import 해서 만든다.
TAG_PROMPT 를 고치면 이미 학습한 어댑터와 입력이 달라지므로 manifest 의 prompt_sha256 으로 확인하고 다시 학습한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.features.persona.agents import TAG_PROMPT, tagging_messages  # noqa: E402
from training.tagging.data import TagExample, load, validate  # noqa: E402


def to_record(ex: TagExample) -> dict:
    return {
        "id": ex.id,
        "prompt": [{"role": "system", "content": TAG_PROMPT}, *tagging_messages(ex.question, ex.answer)],
        "completion": [{"role": "assistant", "content": json.dumps(ex.labels(), ensure_ascii=False)}],
    }


def is_validation(example_id: str, ratio: float) -> bool:
    """id 해시로 나눠서 데이터를 추가해도 기존 예시의 train/val 배정이 바뀌지 않게 한다."""
    bucket = int(hashlib.sha256(example_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return bucket < ratio


def prompt_sha256() -> str:
    return hashlib.sha256(TAG_PROMPT.encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="TagExample JSONL")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "training/tagging/out")
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--allow-unreviewed", action="store_true", help="검수 전 데이터로 파이프라인만 시험할 때")
    args = parser.parse_args()

    examples, errors = load(args.input)
    errors += validate(examples, allow_unreviewed=args.allow_unreviewed)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        print(f"검증 실패 {len(errors)}건. 학습 파일을 만들지 않았습니다.", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    splits: dict[str, list[dict]] = {"train": [], "val": []}
    for ex in examples:
        splits["val" if is_validation(ex.id, args.val_ratio) else "train"].append(to_record(ex))
    for name, rows in splits.items():
        with (args.out / f"{name}.jsonl").open("w", encoding="utf-8") as handle:
            handle.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)

    manifest = {
        "input": str(args.input),
        "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "prompt_sha256": prompt_sha256(),
        "counts": {name: len(rows) for name, rows in splits.items()},
        "sources": sorted({ex.source for ex in examples}),
        "unreviewed_allowed": args.allow_unreviewed,
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
