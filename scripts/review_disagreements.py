"""문장 판정 카드 DISAGREE 검수 — 다수결이 champion 답안지와 다른 문장을 사람이 확인한다 (설계 5.5).

확인된 DISAGREE 만 교정 라벨로 학습에 쓰고 학습 후보 트리거에도 센다. 검수는 시트(CSV)로 한다.

  1) 확인을 기다리는 DISAGREE 를 시트로 뽑는다
       uv run python scripts/review_disagreements.py export data/review/disagree_2026-11-11.csv
  2) decision 열을 채운다
       confirm : 다수결이 맞다 (champion 이 틀렸다) → 교정 라벨로 학습에 쓴다
       reject  : 문장이 애매하거나 다수결이 틀렸다 → 문장을 비활성화해 더 노출하지 않고 학습에서도 뺀다
       (빈 칸) : 이번에는 건너뛴다
  3) 반영한다. 반영 후 학습 후보 조건을 확인하고, 새 후보가 생기면 Discord 로 알린다
       uv run python scripts/review_disagreements.py apply data/review/disagree_2026-11-11.csv --by lena

시트를 뽑은 뒤 새 답변으로 판정(또는 최다 선택)이 바뀐 문장은 반영하지 않는다 — 다시 뽑아서 본다.
decision 에 다른 값이 하나라도 있으면 아무것도 반영하지 않는다.
시트에는 운영 답변으로 만든 다수결이 들어 있다. data/review/ 는 git 에 올라가지 않는다 (.gitignore).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import random
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.features.sentence_cards.models import Sentence  # noqa: E402
from app.features.sentence_cards.repository import SentenceCardRepository  # noqa: E402
from app.features.sentence_cards.schemas import Verdict  # noqa: E402
from app.features.sentence_cards.service import SentenceCardService, build_question  # noqa: E402

COLUMNS = [
    "sentence_id",
    "sentence",
    "question",
    "expected_champion",
    "champion_label",
    "consensus",
    "consensus_label",
    "agreement",
    "decision",
    "note",
]
DECISIONS = {"confirm", "reject"}


def sheet_row(sentence: Sentence) -> dict[str, str]:
    question, choices = build_question(sentence, random.Random(0))
    labels = {c.choice_id.value: c.label for c in choices}
    return {
        "sentence_id": sentence.id,
        "sentence": sentence.sentence,
        "question": question,
        "expected_champion": sentence.expected_champion or "",
        "champion_label": labels.get(sentence.expected_champion or "", ""),
        "consensus": sentence.consensus or "",
        "consensus_label": labels.get(sentence.consensus or "", ""),
        "agreement": f"{sentence.agreement:.2f}" if sentence.agreement is not None else "",
        "decision": "",
        "note": "",
    }


async def export_sheet(db: AsyncSession, path: Path) -> int:
    rows = [sheet_row(s) for s in await SentenceCardRepository(db).pending_disagreements()]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:  # 엑셀에서 한글이 깨지지 않게 BOM
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def read_sheet(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    """(결정이 있는 행, 오류). 오류가 있으면 반영하지 않는다."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    errors, decided = [], []
    for number, row in enumerate(rows, start=2):  # 1행은 헤더
        decision = (row.get("decision") or "").strip().lower()
        if not decision:
            continue
        if decision not in DECISIONS:
            errors.append(f"{number}행: decision 은 confirm · reject · 빈 칸 중 하나여야 합니다 ({decision!r})")
        elif not (row.get("sentence_id") or "").strip():
            errors.append(f"{number}행: sentence_id 가 없습니다")
        else:
            decided.append(row | {"decision": decision})
    return decided, errors


async def apply_decisions(db: AsyncSession, rows: list[dict[str, str]], reviewer: str) -> Counter[str]:
    """결정을 반영하고 학습 후보 조건을 확인한다. 결과별 건수를 돌려준다. 커밋은 호출부가 한다."""
    repo = SentenceCardRepository(db)
    sentences = {s.id: s for s in await repo.sentences_for_update({r["sentence_id"].strip() for r in rows})}
    results: Counter[str] = Counter()
    for row in rows:
        sentence = sentences.get(row["sentence_id"].strip())
        if sentence is None:
            results["not_found"] += 1
        elif sentence.reviewed_by is not None:
            results["already_reviewed"] += 1
        elif sentence.verdict != Verdict.DISAGREE.value or (sentence.consensus or "") != row.get("consensus", ""):
            results["changed_since_export"] += 1
        else:
            sentence.reviewed_by = reviewer
            if row["decision"] == "reject":
                sentence.active = False
            results[row["decision"]] += 1
    await db.flush()
    trigger = await SentenceCardService(db).check_trigger(f"review:{reviewer}")
    if trigger is not None:
        results["trigger_created"] = trigger.seq
    return results


async def _export(path: Path) -> int:
    from app.core.db import SessionLocal

    async with SessionLocal() as db:
        count = await export_sheet(db, path)
    print(f"확인을 기다리는 DISAGREE {count}개 → {path}")
    return 0


async def _apply(path: Path, reviewer: str) -> int:
    from app.core.db import SessionLocal
    from app.features.sentence_cards.notify import discord_sender, notify_pending

    rows, errors = read_sheet(path)
    if errors:
        print("시트 오류가 있어 반영하지 않습니다:", *errors, sep="\n  ", file=sys.stderr)
        return 1
    async with SessionLocal() as db:
        results = await apply_decisions(db, rows, reviewer)
        await db.commit()
    print(dict(results))
    await notify_pending(SessionLocal, discord_sender())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", help="확인을 기다리는 DISAGREE 를 CSV 로")
    export.add_argument("path", type=Path)
    apply = sub.add_parser("apply", help="채운 CSV 를 반영")
    apply.add_argument("path", type=Path)
    apply.add_argument("--by", required=True, help="검수자 (reviewed_by 에 남는다)")
    args = parser.parse_args(argv)
    if args.command == "export":
        return asyncio.run(_export(args.path))
    reviewer = args.by.strip()
    if not 1 <= len(reviewer) <= 64:
        parser.error("--by 는 1~64자")
    return asyncio.run(_apply(args.path, reviewer))


if __name__ == "__main__":
    raise SystemExit(main())
