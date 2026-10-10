#!/usr/bin/env python3
"""Langfuse 품질 데이터셋을 외부 호출 없이 검증한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DATASET_COUNTS = {
    "persona_onboarding_conversation": 60,
    "persona_onboarding_tagging": 80,
    "persona_build": 60,
    "practice_reply": 70,
    "simulation_run": 36,
}
EXPECTED_SPLITS = {"calibration": 61, "regression": 184, "blind_holdout": 61}
REQUIRED_TOP_LEVEL = {"input", "expectedOutput", "metadata"}
REQUIRED_METADATA = {
    "caseId",
    "dataset",
    "split",
    "splitGroup",
    "category",
    "difficulty",
    "sourceRefs",
    "generationMethod",
}
ALLOWED_DIFFICULTIES = {"easy", "medium", "hard"}
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[opusr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)(api[_-]?key|authorization)\s*[:=]\s*[A-Za-z0-9._-]{12,}"),
)


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                errors.append(f"{path.name}:{line_number}: 빈 줄")
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                errors.append(f"{path.name}:{line_number}: JSON 오류: {exc.msg}")
                continue
            if not isinstance(row, dict):
                errors.append(f"{path.name}:{line_number}: 최상위 값이 객체가 아님")
                continue
            rows.append(row)
    return rows, errors


def validate(root: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    counts: dict[str, int] = {}
    split_counts: Counter[str] = Counter()
    difficulties: Counter[str] = Counter()
    categories: dict[str, Counter[str]] = defaultdict(Counter)
    case_ids: set[str] = set()
    input_hashes: dict[str, str] = {}
    split_groups: dict[str, set[str]] = defaultdict(set)

    for dataset, expected_count in DATASET_COUNTS.items():
        path = root / f"{dataset}.jsonl"
        if not path.is_file():
            errors.append(f"필수 파일 없음: {path}")
            continue

        rows, parse_errors = load_jsonl(path)
        errors.extend(parse_errors)
        counts[dataset] = len(rows)
        if len(rows) != expected_count:
            errors.append(f"{dataset}: {len(rows)}건, 기대값 {expected_count}건")

        for index, row in enumerate(rows, start=1):
            location = f"{path.name}:{index}"
            missing_top = REQUIRED_TOP_LEVEL - row.keys()
            if missing_top:
                errors.append(f"{location}: 최상위 필드 누락 {sorted(missing_top)}")
                continue

            input_value = row["input"]
            expected_output = row["expectedOutput"]
            metadata = row["metadata"]
            if not isinstance(input_value, dict) or not input_value:
                errors.append(f"{location}: input은 비어 있지 않은 객체여야 함")
            if not isinstance(expected_output, dict) or not expected_output:
                errors.append(f"{location}: expectedOutput은 비어 있지 않은 객체여야 함")
            if not isinstance(metadata, dict):
                errors.append(f"{location}: metadata는 객체여야 함")
                continue

            missing_meta = REQUIRED_METADATA - metadata.keys()
            if missing_meta:
                errors.append(f"{location}: metadata 필드 누락 {sorted(missing_meta)}")
                continue

            case_id = metadata["caseId"]
            if not isinstance(case_id, str) or not case_id:
                errors.append(f"{location}: caseId가 비어 있음")
            elif case_id in case_ids:
                errors.append(f"{location}: 중복 caseId {case_id}")
            else:
                case_ids.add(case_id)

            if metadata["dataset"] != dataset:
                errors.append(f"{location}: metadata.dataset 불일치 {metadata['dataset']!r}")

            split = metadata["split"]
            if split not in EXPECTED_SPLITS:
                errors.append(f"{location}: 알 수 없는 split {split!r}")
            else:
                split_counts[split] += 1

            split_group = metadata["splitGroup"]
            if not isinstance(split_group, str) or not split_group:
                errors.append(f"{location}: splitGroup이 비어 있음")
            else:
                split_groups[split_group].add(split)

            difficulty = metadata["difficulty"]
            if difficulty not in ALLOWED_DIFFICULTIES:
                errors.append(f"{location}: 알 수 없는 difficulty {difficulty!r}")
            else:
                difficulties[difficulty] += 1

            category = metadata["category"]
            if not isinstance(category, str) or not category:
                errors.append(f"{location}: category가 비어 있음")
            else:
                categories[dataset][category] += 1

            source_refs = metadata["sourceRefs"]
            if (
                not isinstance(source_refs, list)
                or not source_refs
                or not all(isinstance(value, str) and value for value in source_refs)
            ):
                errors.append(f"{location}: sourceRefs는 비어 있지 않은 문자열 배열이어야 함")

            generation_method = metadata["generationMethod"]
            if not isinstance(generation_method, str) or "astra" not in generation_method.lower():
                errors.append(f"{location}: generationMethod에 Astra 생성 근거가 없음")

            input_hash = canonical_hash(input_value)
            previous = input_hashes.get(input_hash)
            if previous:
                errors.append(f"{location}: input 완전 중복, 최초 {previous}")
            else:
                input_hashes[input_hash] = location

            serialized = json.dumps(row, ensure_ascii=False)
            for pattern in SECRET_PATTERNS:
                if pattern.search(serialized):
                    errors.append(f"{location}: 비밀값 형태 문자열 감지")
                    break

    if dict(split_counts) != EXPECTED_SPLITS:
        errors.append(f"전체 split 불일치: {dict(split_counts)}, 기대값 {EXPECTED_SPLITS}")

    leaked_groups = {group: sorted(values) for group, values in split_groups.items() if len(values) > 1}
    if leaked_groups:
        sample = dict(list(leaked_groups.items())[:10])
        errors.append(f"splitGroup 누수 {len(leaked_groups)}건: {sample}")

    total = sum(counts.values())
    if total != sum(DATASET_COUNTS.values()):
        errors.append(f"전체 건수 {total}, 기대값 {sum(DATASET_COUNTS.values())}")

    for dataset, dataset_categories in categories.items():
        if len(dataset_categories) < 3:
            warnings.append(f"{dataset}: category가 {len(dataset_categories)}종뿐임")

    return {
        "ok": not errors,
        "root": str(root),
        "total": total,
        "counts": counts,
        "splits": dict(split_counts),
        "difficulties": dict(difficulties),
        "categoryCounts": {key: dict(value) for key, value in categories.items()},
        "uniqueCaseIds": len(case_ids),
        "uniqueInputs": len(input_hashes),
        "splitGroupLeakCount": len(leaked_groups),
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("evals/quality_datasets"),
        help="JSONL 데이터셋 디렉터리",
    )
    parser.add_argument("--json", action="store_true", help="JSON 형식으로 출력")
    args = parser.parse_args()

    result = validate(args.root)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"quality dataset validation: {'PASS' if result['ok'] else 'FAIL'}")
        print(f"total={result['total']} splits={result['splits']}")
        for error in result["errors"]:
            print(f"ERROR: {error}")
        for warning in result["warnings"]:
            print(f"WARN: {warning}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
