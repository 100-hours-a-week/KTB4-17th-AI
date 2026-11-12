"""문장 적재 스크립트 — 형식 검사, 답안지 변환, 중복·누수 건너뛰기, 실패 재시도. 네트워크 없음 (답안지는 가짜 함수)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.db import Base
from app.features.persona.schemas import Tags
from app.features.sentence_cards.models import Sentence
from app.features.sentence_cards.schemas import ChoiceId
from scripts.gold_drafts import LeakIndex, _audit
from scripts.load_sentences import (
    SeedRow,
    axis_answer,
    direction_answer,
    load_rows,
    main,
    plan_rows,
    read_rows,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "data/sentences/sentences.example.jsonl"


def _row(**extra) -> dict:
    return {
        "sentence": "답장은 생각날 때 몰아서 하는 편이에요",
        "question_type": "AXIS",
        "dimension": "contact_rhythm",
        "source": "team_written",
    } | extra


def _write(path: Path, rows: list) -> Path:
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)) + "\n" for r in rows))
    return path


def _index(*texts: str) -> LeakIndex:
    index = LeakIndex.__new__(LeakIndex)
    index.audit, index.texts = _audit(), {}
    for text in texts:
        index.add_text(text, "gold.jsonl")
    return index


@pytest.fixture
def factory():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(setup())
    yield async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(engine.dispose())


def _sentences(factory) -> list[Sentence]:
    async def fetch():
        async with factory() as db:
            return list((await db.execute(select(Sentence).order_by(Sentence.sentence))).scalars())

    return asyncio.run(fetch())


# ── 형식 ──────────────────────────────────────────────────


def test_example_file_is_valid():
    rows, errors = read_rows(EXAMPLE)
    assert errors == []
    assert len(rows) == 4


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (_row(sentence="짧아요"), "sentence"),
        (_row(sentence="가" * 81), "sentence"),
        (_row(dimension="unknown_dim"), "등록되지 않은 차원"),
        (_row(question_type="DIRECTION", dimension="interests"), "방향 질문은 점수 차원만"),
        (_row(is_check=True), "check_answer"),
        (_row(is_check=True, check_answer="unsure"), "check_answer"),
        (_row(question_type="DIRECTION", dimension="avoidance", is_check=True, check_answer="direct"), "check_answer"),
        (_row(check_answer="direct"), "check_answer 를 비워"),
        (_row(source="user_clone"), "source"),
        (_row(extra="x"), "extra"),
    ],
)
def test_invalid_rows_are_reported_with_line_number(tmp_path, row, message):
    rows, errors = read_rows(_write(tmp_path / "s.jsonl", [_row(), row]))
    assert len(rows) == 1
    assert len(errors) == 1 and errors[0].startswith("2번째 줄") and message in errors[0]


def test_broken_json_is_an_error(tmp_path):
    _, errors = read_rows(_write(tmp_path / "s.jsonl", ["{not json"]))
    assert errors and "JSON 형식 오류" in errors[0]


def test_whitespace_is_normalized():
    row = SeedRow.model_validate(_row(sentence="  답장은   생각날 때\n몰아서 하는 편이에요 "))
    assert row.sentence == "답장은 생각날 때 몰아서 하는 편이에요"


def test_main_stops_on_format_error_before_touching_db(tmp_path, capsys):
    assert main([str(_write(tmp_path / "s.jsonl", [_row(sentence="짧아요")]))]) == 1
    assert "형식 오류" in capsys.readouterr().err


def test_main_refuses_openrouter_without_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(get_settings(), "llm_base_url", "https://openrouter.ai/api/v1")
    assert main([str(_write(tmp_path / "s.jsonl", [_row()]))]) == 2
    assert "--allow-openrouter" in capsys.readouterr().err


# ── 답안지 ────────────────────────────────────────────────


def test_axis_answer_maps_tagger_output():
    assert axis_answer(Tags(primary=["contact_rhythm"]), "contact_rhythm") == ChoiceId.DIRECT
    assert axis_answer(Tags(secondary=["contact_rhythm"]), "contact_rhythm") == ChoiceId.INFERRED
    assert axis_answer(Tags(primary=["avoidance"]), "contact_rhythm") == ChoiceId.NONE
    assert axis_answer(Tags(off_topic=True), "contact_rhythm") == ChoiceId.NONE


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (100, "high"),
        (65, "high"),
        (64, "unsure"),
        (50, "unsure"),
        (35, "unsure"),
        (34, "low"),
        (0, "low"),
        (None, "unsure"),
    ],
)
def test_direction_answer_uses_score_ranges(score, expected):
    assert direction_answer(score) == ChoiceId(expected)


# ── 계획 · 적재 ───────────────────────────────────────────


def test_plan_skips_loaded_duplicate_and_gold_leak(factory):
    gold = "휴일에도 제 작업실에서 혼자 보내는 시간을 꼭 남겨 두고 싶어요."
    rows = [
        SeedRow.model_validate(_row()),
        SeedRow.model_validate(_row(sentence="답장은 생각날 때  몰아서 하는 편이에요")),  # 파일 안 중복
        SeedRow.model_validate(_row(question_type="DIRECTION", dimension="contact_rhythm")),  # 질문이 다르면 별개
        SeedRow.model_validate(_row(sentence=gold, dimension="avoidance")),
        SeedRow.model_validate(_row(sentence="주말엔 거의 집에서 혼자 쉬어요", dimension="avoidance")),
    ]

    async def run():
        async with factory() as db:
            db.add(rows[4].to_model(ChoiceId.NONE))
            await db.commit()
            return await plan_rows(rows, db, _index(gold))

    plan = asyncio.run(run())
    assert [r.question_type for r in plan.to_load] == ["AXIS", "DIRECTION"]
    assert [reason for _, reason in plan.skipped] == ["duplicate_in_file", "gold_leak:gold.jsonl", "already_loaded"]


def test_load_stores_answer_key_and_retries_failures_on_next_run(factory):
    rows = [
        SeedRow.model_validate(_row(family_id="fam-1")),
        SeedRow.model_validate(
            _row(question_type="DIRECTION", dimension="anxiety", is_check=True, check_answer="high")
        ),
    ]
    calls = []

    def answer_key(results):
        async def key(row):
            calls.append(row.question_type.value)
            value = results[row.question_type.value]
            if isinstance(value, Exception):
                raise value
            return value

        return key

    async def run(key):
        async with factory() as db:
            plan = await plan_rows(rows, db, _index())
            return await load_rows(plan.to_load, db, key)

    loaded, failed = asyncio.run(run(answer_key({"AXIS": ChoiceId.DIRECT, "DIRECTION": RuntimeError("down")})))
    assert [s.sentence for s in loaded] == [rows[0].sentence] and failed == [rows[1]]

    stored = _sentences(factory)
    assert len(stored) == 1
    assert stored[0].expected_champion == "direct"
    assert stored[0].expected_teacher is None
    assert stored[0].family_id == "fam-1"
    assert stored[0].verdict == "PENDING" and stored[0].active

    calls.clear()
    loaded, failed = asyncio.run(run(answer_key({"AXIS": ChoiceId.NONE, "DIRECTION": ChoiceId.HIGH})))
    assert calls == ["DIRECTION"]  # 이미 넣은 문장은 다시 계산하지 않는다
    assert failed == []
    check = next(s for s in _sentences(factory) if s.is_check)
    assert (check.check_answer, check.expected_champion) == ("high", "high")
    assert check.family_id  # 없으면 문장 해시로 채운다
