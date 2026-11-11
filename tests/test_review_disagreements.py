"""DISAGREE 검수 시트 — 뽑기 · 읽기 · 반영 · 반영 후 학습 후보. in-memory SQLite, 네트워크 없음."""

from __future__ import annotations

import asyncio
import csv
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.features.sentence_cards import verdict
from app.features.sentence_cards.models import Sentence, SentenceTrainingTrigger
from scripts.review_disagreements import COLUMNS, apply_decisions, export_sheet, main, read_sheet


@pytest.fixture
def factory():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(setup())
    yield async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(engine.dispose())


def _seed(factory, **rows: dict) -> None:
    async def insert():
        async with factory() as db:
            for sid, row in rows.items():
                defaults = {
                    "family_id": "f",
                    "source": "team_written",
                    "question_type": "DIRECTION",
                    "dimension": "avoidance",
                    "expected_champion": "high",
                    "consensus": "low",
                    "verdict": "DISAGREE",
                    "agreement": 0.8,
                    "sentence": f"{sid} 문장이에요",
                }
                db.add(Sentence(id=sid, **(defaults | row)))
            await db.commit()

    asyncio.run(insert())


def _get(factory, sid: str) -> Sentence:
    async def fetch():
        async with factory() as db:
            return await db.get(Sentence, sid)

    return asyncio.run(fetch())


def _apply(factory, rows, reviewer="lena"):
    async def run():
        async with factory() as db:
            results = await apply_decisions(db, rows, reviewer)
            await db.commit()
            return results

    return asyncio.run(run())


def _export(factory, path: Path) -> list[dict[str, str]]:
    async def run():
        async with factory() as db:
            return await export_sheet(db, path)

    asyncio.run(run())
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows([{c: r.get(c, "") for c in COLUMNS} for r in rows])
    return path


def test_export_lists_only_pending_trainable_disagreements(factory, tmp_path):
    _seed(
        factory,
        weak={"agreement": 0.7},
        strong={"agreement": 0.9},
        reviewed={"reviewed_by": "lena"},
        inactive={"active": False},
        check={"is_check": True, "check_answer": "high"},
        agree={"verdict": "AGREE", "consensus": "high"},
    )
    rows = _export(factory, tmp_path / "sheet.csv")
    assert [r["sentence_id"] for r in rows] == ["strong", "weak"]  # 일치율 높은 것부터
    row = rows[0]
    assert row["question"] == "이런 말을 하는 사람은 어느 쪽일까요?"
    assert (row["champion_label"], row["consensus_label"]) == (
        "각자 생활을 중시하고 독립적 거리를 유지",
        "웬만한 건 공유하고 함께 있는 시간을 선호",
    )
    assert (row["agreement"], row["decision"]) == ("0.90", "")


def test_read_sheet_skips_blank_and_rejects_unknown_decision(tmp_path):
    path = _write(
        tmp_path / "s.csv",
        [
            {"sentence_id": "a", "decision": " Confirm "},
            {"sentence_id": "b", "decision": ""},
            {"sentence_id": "c", "decision": "maybe"},
            {"sentence_id": "", "decision": "reject"},
        ],
    )
    rows, errors = read_sheet(path)
    assert [(r["sentence_id"], r["decision"]) for r in rows] == [("a", "confirm")]
    assert len(errors) == 2 and errors[0].startswith("4행") and errors[1].startswith("5행")


def test_main_applies_nothing_when_sheet_has_errors(tmp_path, capsys):
    path = _write(tmp_path / "s.csv", [{"sentence_id": "a", "decision": "maybe"}])
    assert main(["apply", str(path), "--by", "lena"]) == 1
    assert "반영하지 않습니다" in capsys.readouterr().err


def test_apply_confirms_rejects_and_skips_stale_rows(factory, tmp_path):
    _seed(factory, ok={}, bad={}, moved={}, done={"reviewed_by": "kim"})
    rows = _export(factory, tmp_path / "sheet.csv")
    decisions = {"ok": "confirm", "bad": "reject", "moved": "confirm"}
    filled = [r | {"decision": decisions.get(r["sentence_id"], "")} for r in rows]
    filled.append({"sentence_id": "done", "consensus": "low", "decision": "confirm"})
    filled.append({"sentence_id": "ghost", "consensus": "low", "decision": "confirm"})

    async def change_vote():  # 시트를 뽑은 뒤 새 답변으로 최다 선택이 바뀌었다
        async with factory() as db:
            (await db.get(Sentence, "moved")).consensus = "unsure"
            await db.commit()

    asyncio.run(change_vote())
    rows, errors = read_sheet(_write(tmp_path / "filled.csv", filled))
    assert errors == []
    results = _apply(factory, rows)

    assert dict(results) == {
        "confirm": 1,
        "reject": 1,
        "changed_since_export": 1,
        "already_reviewed": 1,
        "not_found": 1,
    }
    ok, bad, moved, done = (_get(factory, s) for s in ("ok", "bad", "moved", "done"))
    assert (ok.reviewed_by, ok.active) == ("lena", True)
    assert (bad.reviewed_by, bad.active) == ("lena", False)  # 더 노출하지 않고 학습에서도 뺀다
    assert moved.reviewed_by is None
    assert done.reviewed_by == "kim"


def test_apply_creates_training_trigger_when_review_fills_condition(factory, monkeypatch):
    monkeypatch.setattr(verdict, "TRIGGER_AGREE", 1)
    monkeypatch.setattr(verdict, "TRIGGER_DISAGREE_REVIEWED", 1)
    _seed(factory, agreed={"verdict": "AGREE", "consensus": "high"}, wrong={})

    results = _apply(factory, [{"sentence_id": "wrong", "consensus": "low", "decision": "confirm"}])
    assert results["trigger_created"] == 1

    async def triggers():
        async with factory() as db:
            return list((await db.execute(select(SentenceTrainingTrigger))).scalars())

    (trigger,) = asyncio.run(triggers())
    assert (trigger.source, trigger.agree, trigger.disagree_reviewed, trigger.notified_at) == (
        "review:lena",
        1,
        1,
        None,
    )
