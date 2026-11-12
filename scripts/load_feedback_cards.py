"""개인정보와 소유자 검토를 마친 피드백 후보 JSONL을 적재한다.

uv run python scripts/load_feedback_cards.py cards.jsonl --dry-run
uv run python scripts/load_feedback_cards.py cards.jsonl

한 줄은 card_id, user_id, context_question(null 허용), utterance, expires_at을 담는다.
card_id는 재실행에도 같은 값을 쓴다. 같은 ID의 내용이 다르면 전체 적재를 롤백한다.
상대방 대화 원문을 넣지 않는다. context_question은 검토한 중립 질문만 쓴다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal  # noqa: E402
from app.features.feedback_cards.models import QUESTION, FeedbackCard  # noqa: E402
from app.features.feedback_cards.schemas import Identifier, ShortText, Utterance  # noqa: E402


class SeedCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_id: Identifier
    user_id: Identifier
    context_question: ShortText | None
    utterance: Utterance
    expires_at: AwareDatetime


def read_cards(path: Path) -> list[SeedCard]:
    rows = [
        SeedCard.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len({row.card_id for row in rows}) != len(rows):
        raise ValueError("파일 안에 중복 card_id가 있습니다")
    if any(row.expires_at <= datetime.now(UTC) for row in rows):
        raise ValueError("expires_at은 미래 시각이어야 합니다")
    return rows


async def load_cards(db: AsyncSession, rows: list[SeedCard]) -> int:
    inserted = 0
    for row in rows:
        existing = await db.get(FeedbackCard, row.card_id)
        if existing is not None:
            # 노출 시 만료 시각이 짧아질 수 있으므로 기존 만료/상태는 덮어쓰지 않는다.
            if (existing.user_id, existing.context_question, existing.utterance) != (
                row.user_id,
                row.context_question,
                row.utterance,
            ):
                raise ValueError(f"기존 card_id의 내용이 다릅니다: {row.card_id}")
            continue
        db.add(
            FeedbackCard(
                id=row.card_id,
                user_id=row.user_id,
                context_question=row.context_question,
                utterance=row.utterance,
                question=QUESTION,
                expires_at=row.expires_at.astimezone(UTC),
            )
        )
        inserted += 1
    await db.flush()
    return inserted


async def _load(rows: list[SeedCard]) -> int:
    async with SessionLocal.begin() as db:
        return await load_cards(db, rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        rows = read_cards(args.path)
        if args.dry_run:
            print(f"형식 검증 통과: {len(rows)}장")
        else:
            print(f"적재 완료: {asyncio.run(_load(rows))}장")
    except (OSError, ValueError, ValidationError) as e:
        parser.exit(1, f"{e}\n")


if __name__ == "__main__":
    main()
