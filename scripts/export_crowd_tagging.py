"""문장 판정 카드 다수결 → 태깅 학습 데이터 · 다수결 평가셋. HF private dataset 에 버전으로 올린다 (설계 5.6 · 5.7).

축 질문 카드는 문장 하나에 차원 하나만 묻는다. 그 한 차원 라벨만으로 예시를 만들면 묻지 않은 차원이 정답에서 빠져
모델이 "그 차원은 태그하지 마"를 배운다. 그래서 적재 때 저장한 champion 태거 전체 출력(champion_tags)을 두고,
판정한 차원만 다수결로 고친다 (direct → primary, inferred → secondary, none → 뺀다).

  쓰는 판정  : AGREE (reviewed_by = "crowd"), 사람이 confirm 한 DISAGREE (reviewed_by = 검수자)
  안 쓰는 것 : AMBIGUOUS · PENDING (그 차원은 champion 출력 그대로), 검증 문항, 비활성 문장, 방향 질문
  학습 · 평가 분리: family_id 해시로 EVAL_RATIO 만큼을 평가셋으로 뺀다. 평가 묶음 문장은 학습에 들어가지 않는다

  uv run python scripts/export_crowd_tagging.py --out-dir data/crowd/export --no-upload     # 로컬에만
  uv run python scripts/export_crowd_tagging.py --out-dir data/crowd/export --repo <org>/ktb-crowd-tagging

출력 (공개 레포에 올리지 않는다. data/crowd/ 는 .gitignore):
  train.jsonl    training/tagging/data.py 의 TagExample (source = "crowd")
  eval.jsonl     (문장, 차원)별 다수결. scripts/eval_crowd_tagging.py 가 채점한다. AMBIGUOUS 도 넣는다
  manifest.json  개수 · 해시 · 마지막 학습 후보 번호
사용자 id 는 담지 않는다. 문장은 팀 작성 · 합성 문장이다.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.features.sentence_cards.models import Sentence  # noqa: E402
from app.features.sentence_cards.repository import SentenceCardRepository  # noqa: E402
from app.features.sentence_cards.schemas import ChoiceId, QuestionType, Verdict  # noqa: E402
from training.tagging.build_sft import is_validation  # noqa: E402
from training.tagging.data import TagExample, validate  # noqa: E402

EVAL_RATIO = 0.2
CROWD_REVIEWER = "crowd"


def judged_label(sentence: Sentence) -> tuple[str, str] | None:
    """(라벨, 확인한 주체). 학습에 쓸 수 없는 판정이면 None."""
    if sentence.verdict == Verdict.AGREE.value and sentence.consensus:
        return sentence.consensus, CROWD_REVIEWER
    if sentence.verdict == Verdict.DISAGREE.value and sentence.reviewed_by and sentence.consensus:
        return sentence.consensus, sentence.reviewed_by
    return None


def correct_tags(base: dict, judged: dict[str, str]) -> dict:
    """champion 전체 출력에서 판정한 차원만 다수결로 바꾼다. 나머지 차원은 그대로 둔다."""
    primary = [d for d in base.get("primary", []) if d not in judged]
    secondary = [d for d in base.get("secondary", []) if d not in judged]
    for dimension, label in sorted(judged.items()):
        if label == ChoiceId.DIRECT:
            primary.append(dimension)
        elif label == ChoiceId.INFERRED:
            secondary.append(dimension)
    # 사람들이 어느 차원 이야기라고 했으면 주제 밖일 수 없다
    off_topic = bool(base.get("off_topic")) and not (primary or secondary)
    return {"primary": primary, "secondary": secondary, "off_topic": off_topic}


def _input_key(sentence: Sentence) -> tuple[str, str]:
    return " ".join(sentence.sentence.split()), sentence.context_question or ""


def build(sentences: list[Sentence], eval_ratio: float = EVAL_RATIO) -> tuple[list[TagExample], list[dict]]:
    """(학습 예시, 평가 행). 같은 문장 · 같은 질문의 여러 차원 카드는 예시 하나로 합친다."""
    by_input: dict[tuple[str, str], list[Sentence]] = defaultdict(list)
    for sentence in sentences:
        by_input[_input_key(sentence)].append(sentence)

    examples: list[TagExample] = []
    eval_rows: list[dict] = []
    for (answer, question), group in by_input.items():
        group.sort(key=lambda s: (s.created_at, s.id))
        family = group[0].family_id
        if is_validation(family, eval_ratio):
            for s in group:
                label = judged_label(s)
                if label is None and s.verdict != Verdict.AMBIGUOUS.value:
                    continue
                eval_rows.append(
                    {
                        "id": f"crowd-eval-{s.id}",
                        "question": question,
                        "answer": answer,
                        "dimension": s.dimension,
                        "label": label[0] if label else None,  # AMBIGUOUS 는 정답 없이 확신 비율만 본다
                        "verdict": s.verdict,
                        "family_id": family,
                    }
                )
            continue

        judged: dict[str, str] = {}
        reviewers: set[str] = set()
        for s in group:
            if (label := judged_label(s)) is not None:
                judged[s.dimension] = label[0]
                reviewers.add(label[1])
        if not judged:
            continue
        tags = correct_tags(group[0].champion_tags or {}, judged)
        digest = hashlib.sha256(f"{question}\n{answer}".encode()).hexdigest()[:16]
        examples.append(
            TagExample(
                id=f"crowd-{digest}",
                question=question,
                answer=answer,
                source="crowd",
                reviewed_by="+".join(sorted(reviewers)),
                group=family,
                **tags,
            )
        )
    return examples, eval_rows


async def load_sentences(db: AsyncSession) -> list[Sentence]:
    stmt = select(Sentence).where(
        Sentence.question_type == QuestionType.AXIS.value,
        Sentence.active.is_(True),
        Sentence.is_check.is_(False),
        Sentence.champion_tags.is_not(None),
        Sentence.context_question.is_not(None),
        Sentence.verdict.in_([Verdict.AGREE.value, Verdict.DISAGREE.value, Verdict.AMBIGUOUS.value]),
    )
    return list((await db.execute(stmt)).scalars())


def write(out_dir: Path, examples: list[TagExample], eval_rows: list[dict], trigger_seq: int | None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    train = "".join(ex.model_dump_json(exclude_none=True) + "\n" for ex in examples)
    evals = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in eval_rows)
    (out_dir / "train.jsonl").write_text(train, encoding="utf-8")
    (out_dir / "eval.jsonl").write_text(evals, encoding="utf-8")
    manifest = {
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "trigger_seq": trigger_seq,
        "eval_ratio": EVAL_RATIO,
        "train": {"count": len(examples), "sha256": hashlib.sha256(train.encode()).hexdigest()},
        "eval": {
            "count": len(eval_rows),
            "by_verdict": dict(Counter(r["verdict"] for r in eval_rows)),
            "sha256": hashlib.sha256(evals.encode()).hexdigest(),
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def upload(out_dir: Path, repo: str, tag: str) -> None:
    from huggingface_hub import HfApi

    api = HfApi()  # HF_TOKEN 환경변수
    api.create_repo(repo, repo_type="dataset", private=True, exist_ok=True)
    if not api.repo_info(repo, repo_type="dataset").private:
        raise SystemExit(f"{repo} 가 공개 리포입니다. 운영 다수결 데이터는 private 리포에만 올립니다")
    api.upload_folder(repo_id=repo, repo_type="dataset", folder_path=str(out_dir), commit_message=f"crowd export {tag}")
    api.create_tag(repo, tag=tag, repo_type="dataset")


async def _run(args: argparse.Namespace) -> int:
    from app.core.db import SessionLocal

    async with SessionLocal() as db:
        sentences = await load_sentences(db)
        last = await SentenceCardRepository(db).last_trigger()
    examples, eval_rows = build(sentences)
    errors = validate(examples)  # 평가 골드셋과 같은 답변 · 형식 오류는 내보내기 전에 막는다
    if errors:
        print("학습 데이터 검증 실패:", *errors, sep="\n  ", file=sys.stderr)
        return 1
    manifest = write(args.out_dir, examples, eval_rows, last.seq if last else None)
    print(json.dumps(manifest, ensure_ascii=False))
    if args.no_upload:
        return 0
    tag = args.tag or f"crowd-{datetime.now(UTC):%Y%m%d}-t{last.seq if last else 0}"
    upload(args.out_dir, args.repo, tag)
    print(f"uploaded {args.repo}@{tag}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "data/crowd/export")
    parser.add_argument("--repo", help="HF private dataset 리포 (<org>/<name>)")
    parser.add_argument("--tag", help="기본 crowd-<날짜>-t<학습 후보 번호>")
    parser.add_argument("--no-upload", action="store_true")
    args = parser.parse_args(argv)
    if not args.no_upload and not args.repo:
        parser.error("--repo 를 주거나 --no-upload 로 로컬에만 쓰세요")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
