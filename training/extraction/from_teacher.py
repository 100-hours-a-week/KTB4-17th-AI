"""teacher 추출 라벨(scripts/label_with_teacher.py 의 extraction.jsonl) → TRL 학습용 prompt/completion JSONL.

    uv run python training/extraction/from_teacher.py data/teacher/pilot200/extraction.jsonl \\
        --out training/extraction/out
    python training/tagging/train_lora.py --base-model kakaocorp/kanana-2-3b-instruct --max-length 4096 \\
        --batch-size 1 --grad-accum 16 --data-dir training/extraction/out --output-dir training/extraction/adapters/v1

prompt 는 서빙 코드(RUBRIC, ExtractionAgent 의 대화록·허용 차원 형식)를 그대로 import 해서 만든다.
출력 폴더 구성(train.jsonl · val.jsonl · manifest.json)은 training/tagging/build_sft.py 와 같아서 train_lora.py 로 학습한다.

옮기지 않는 것:
  - 골드셋(source=gold) 행 — 평가 데이터셋이라 학습에 넣으면 누수다
  - 호출·파싱 실패 행, 스키마 오류 행
  - 사용자 답 중 하나라도 평가 데이터셋 답변과 같은 세션 (training/tagging/data.py 의 누수 규칙과 같음)
정리하는 것:
  - 정답에서 허용 차원(answered) 밖 점수·텍스트 키와 스키마에 없는 키를 지운다. 운영 후처리가 어차피 지우는 값이라
    처음부터 "모르는 차원은 키를 생략"하도록 가르친다
나누는 것:
  - familyId(시드 프로필) 단위로 train/val 을 나눈다. 같은 시드의 세션이 양쪽에 있으면 검증 손실이 낙관적으로 나온다

teacher 라벨은 사람 검수 전이므로 manifest 의 unreviewed_allowed 는 항상 true 다(train_lora.py 가 경고를 띄운다).
라벨을 만든 뒤 RUBRIC 이 바뀌었으면 멈춘다 — label_with_teacher.py 로 다시 라벨링한다.
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
from app.features.persona.schemas import SCORED, TEXTUAL, RawExtraction  # noqa: E402
from training.tagging.build_sft import is_validation  # noqa: E402
from training.tagging.data import eval_answers, normalize  # noqa: E402


def prompt_sha256() -> str:
    return hashlib.sha256(RUBRIC.encode()).hexdigest()


def user_content(history: list[dict], answered: set[str]) -> str:
    """ExtractionAgent.extract 가 answered 를 받았을 때 보내는 user 메시지와 같다."""
    return ExtractionAgent._transcript(history) + ExtractionAgent._answered_section(answered)


def clean_target(raw: dict, answered: set[str]) -> dict:
    """허용 차원 밖 점수·텍스트와 스키마에 없는 키를 지운다. 키 순서는 teacher 원출력을 따른다."""
    known = set(RawExtraction.model_fields)
    cleaned = {}
    for key, value in raw.items():
        if key not in known:
            continue
        if key in SCORED and (value is None or key not in answered):
            continue
        if key in TEXTUAL and (not value or key not in answered):
            continue
        cleaned[key] = value
    return cleaned


def convert(row: dict, *, leaked: set[str]) -> tuple[dict | None, str]:
    """teacher 추출 한 행 → (학습 레코드, 건너뛴 이유). 옮기면 이유는 빈 문자열."""
    if row.get("source") != "synthetic":
        return None, "합성 데이터가 아님"
    if any(normalize(m["content"]) in leaked for m in row["input"]["history"] if m["role"] == "user"):
        return None, "평가 데이터셋과 같은 답변 (평가 누수)"
    if "output" not in row:
        return None, "호출·파싱 실패"
    if row.get("schemaError"):
        return None, "스키마 오류"
    answered = set(row["input"]["answered"])
    return {
        "id": row["id"],
        "familyId": row["familyId"],
        "prompt": [
            {"role": "system", "content": RUBRIC},
            {"role": "user", "content": user_content(row["input"]["history"], answered)},
        ],
        "completion": [
            {"role": "assistant", "content": json.dumps(clean_target(row["output"], answered), ensure_ascii=False)}
        ],
    }, ""


def prompt_drift(rows: list[dict]) -> set[str]:
    """라벨을 만들 때의 RUBRIC 해시(앞 12자리) 중 지금 RUBRIC 과 다른 것."""
    current = prompt_sha256()
    versions = {(row.get("teacher") or {}).get("extractPromptVersion") for row in rows}
    return {v for v in versions if v and not current.startswith(v)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="label_with_teacher.py 의 extraction.jsonl")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "training/extraction/out")
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--allow-prompt-drift", action="store_true", help="RUBRIC 이 바뀌었어도 만든다 (시험용)")
    args = parser.parse_args(argv)

    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    drift = prompt_drift(rows)
    if drift and not args.allow_prompt_drift:
        print(
            f"라벨의 RUBRIC({', '.join(sorted(drift))})이 지금 RUBRIC({prompt_sha256()[:12]})과 다릅니다. "
            "label_with_teacher.py 로 다시 라벨링하세요.",
            file=sys.stderr,
        )
        return 1

    splits: dict[str, list[dict]] = {"train": [], "val": []}
    skipped: Counter[str] = Counter()
    leaked = eval_answers()
    for row in rows:
        record, reason = convert(row, leaked=leaked)
        if record is None:
            skipped[reason] += 1
            continue
        splits["val" if is_validation(record.pop("familyId"), args.val_ratio) else "train"].append(record)

    args.out.mkdir(parents=True, exist_ok=True)
    for name, records in splits.items():
        with (args.out / f"{name}.jsonl").open("w", encoding="utf-8") as handle:
            handle.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    manifest = {
        "input": str(args.input),
        "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "prompt_sha256": prompt_sha256(),
        "label_prompt_versions": sorted({(r.get("teacher") or {}).get("extractPromptVersion", "") for r in rows}),
        "counts": {name: len(records) for name, records in splits.items()},
        "skipped": dict(skipped),
        "sources": ["synthetic"],
        "unreviewed_allowed": True,
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
