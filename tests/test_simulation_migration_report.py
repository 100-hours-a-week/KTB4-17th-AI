"""시뮬레이션 마이그레이션 리포트 도구 단위 테스트."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from conftest import seed_persona, with_db

import app.features.simulation_migration.models  # noqa: F401  Base.metadata에 테이블 등록
from app.features.persona.schemas import Narrative, PersonaResponse
from app.features.simulation.schemas import (
    DateSuggestion,
    Grade,
    Highlight,
    MatchingReport,
    Overall,
    ReportConfidence,
)
from app.features.simulation_migration.manager import RunManager
from app.features.simulation_migration.report_tool import write_matching_report
from app.features.simulation_migration.repository import MigrationRepository


def _make_personas() -> tuple[PersonaResponse, PersonaResponse]:
    p_a = PersonaResponse(
        persona_id="p-alpha",
        scores={},
        interests=["운동", "러닝"],
        routine=["아침 운동"],
        date_prefer=["야외 데이트"],
        narrative=Narrative(headline="운동가", body="운동본문", traits=["활발"]),
    )
    p_b = PersonaResponse(
        persona_id="p-beta",
        scores={},
        interests=["독서", "카페"],
        routine=["주말 독서"],
        date_prefer=["조용한 카페"],
        narrative=Narrative(headline="독서가", body="독서본문", traits=["차분"]),
    )
    return p_a, p_b


def _make_mock_report(
    *,
    simulation_id: str = "sim-123",
    headline: str = "원래 헤드라인",
    highlights: list[Highlight] | None = None,
    narrative_source: str = "llm",
) -> MatchingReport:
    return MatchingReport(
        simulation_id=simulation_id,
        persona_a_id="p-alpha",
        persona_b_id="p-beta",
        overall=Overall(
            score=80,
            grade=Grade.GOOD,
            grade_label="잘 맞아요",
            headline=headline,
            summary="둘은 아주 잘 맞습니다.",
        ),
        areas=[],
        highlights=highlights or [],
        strengths=["대화가 잘 통함"],
        cautions=["연락 주기 맞추기"],
        risks=[],
        date_suggestion=DateSuggestion(suggested=["카페"], avoid=[], comment="조용한 곳 추천"),
        confidence=ReportConfidence(accuracy=80, low_dimensions=[]),
        narrative_source=narrative_source,  # type: ignore[arg-type]
    )


def test_write_matching_report_filters_out_of_bounds_highlights(monkeypatch: pytest.MonkeyPatch) -> None:
    """범위 밖(turn_index < 0 또는 >= len(transcript)) highlight는 필터링되어 저장본에 없다."""
    pa, pb = _make_personas()
    transcript = [
        ("a", "대사 0"),
        ("b", "대사 1"),
        ("a", "대사 2"),
    ]  # 길이 3 (인덱스 0, 1, 2)

    raw_highlights = [
        Highlight(kind="click", turn_index=-1, quote="음수 인덱스", why="이유"),
        Highlight(kind="click", turn_index=0, quote="정상 0", why="이유"),
        Highlight(kind="friction", turn_index=1, quote="정상 1", why="이유"),
        Highlight(kind="click", turn_index=3, quote="범위 밖 3", why="이유"),
        Highlight(kind="friction", turn_index=10, quote="범위 밖 10", why="이유"),
    ]

    async def mock_build_report(inp: Any, agent: Any = None, **kw: Any) -> MatchingReport:
        return _make_mock_report(highlights=list(raw_highlights))

    monkeypatch.setattr("app.features.simulation_migration.report_tool.build_report", mock_build_report)

    state = {
        "persona_a": pa,
        "persona_b": pb,
        "nickname_a": "알파",
        "nickname_b": "베타",
        "transcript": transcript,
        "run_id": "run-test-hl",
    }

    result = asyncio.run(write_matching_report(state))

    saved_highlights = result["highlights"]
    saved_turn_indices = [h["turn_index"] for h in saved_highlights]
    assert saved_turn_indices == [0, 1]
    assert len(saved_highlights) == 2


def test_build_report_template_fallback_gives_done_and_template_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_report가 예외를 내면 agent=None으로 한 번 더 호출해 template MatchingReport를 얻고 narrative_source는 template이다."""
    pa, pb = _make_personas()
    transcript = [("a", "안녕"), ("b", "반가워")]

    call_count = 0

    async def mock_build_report(inp: Any, agent: Any = None, **kw: Any) -> MatchingReport:
        nonlocal call_count
        call_count += 1
        if agent is not None:
            raise RuntimeError("LLM 호출 실패 시뮬레이션")
        return _make_mock_report(narrative_source="template", headline="템플릿 헤드라인")

    monkeypatch.setattr("app.features.simulation_migration.report_tool.build_report", mock_build_report)

    state = {
        "persona_a": pa,
        "persona_b": pb,
        "nickname_a": "알파",
        "nickname_b": "베타",
        "transcript": transcript,
        "run_id": "run-fallback",
    }

    result = asyncio.run(write_matching_report(state))

    assert call_count == 2
    assert result["narrative_source"] == "template"
    assert result["overall"]["headline"] == "템플릿 헤드라인"


def test_perspective_swap_true_status_done_and_validation_json_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """perspective_swap이 True여도 headline은 그대로이고 validation JSON만 {"perspective_swap": true}이다."""
    pa, pb = _make_personas()
    transcript = [("a", "안녕"), ("b", "반가워")]

    async def mock_build_report(inp: Any, agent: Any = None, **kw: Any) -> MatchingReport:
        return _make_mock_report(headline="원래 헤드라인 보존")

    monkeypatch.setattr("app.features.simulation_migration.report_tool.build_report", mock_build_report)
    monkeypatch.setattr(
        "app.features.simulation_migration.report_tool._report_perspective_swap",
        lambda *args, **kwargs: True,
    )

    state = {
        "persona_a": pa,
        "persona_b": pb,
        "nickname_a": "알파",
        "nickname_b": "베타",
        "transcript": transcript,
        "run_id": "run-swap",
    }

    result = asyncio.run(write_matching_report(state))

    assert result["overall"]["headline"] == "원래 헤드라인 보존"
    assert result["validation"] == {"perspective_swap": True}


def test_injected_write_report_skips_build_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """주입된 write_report가 있으면 build_report가 전혀 호출되지 않는다."""

    build_report_called = False

    async def mock_build_report(*args: Any, **kwargs: Any) -> MatchingReport:
        nonlocal build_report_called
        build_report_called = True
        raise AssertionError("build_report should not be called when write_report is injected")

    monkeypatch.setattr("app.features.simulation_migration.report_tool.build_report", mock_build_report)

    async def scenario(factory: Any) -> None:
        async with factory() as db:
            await seed_persona(db, persona_id="p-alpha", user_id="u-a", nickname="A")
            await seed_persona(db, persona_id="p-beta", user_id="u-b", nickname="B")

        pa, pb = _make_personas()
        injected_called = False

        async def custom_write_report(state: Any) -> dict[str, Any]:
            nonlocal injected_called
            injected_called = True
            return {"summary": "커스텀 리포트", "narrative_source": "custom"}

        # 4번 발화 후 report 노드로 가는 가짜 그래프
        class MockGraph:
            def __init__(self, write_rep: Any) -> None:
                self.write_rep = write_rep

            async def astream(self, initial_state: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
                yield {"speak_a": {"transcript": [("a", "1")]}}
                yield {"speak_b": {"transcript": [("a", "1"), ("b", "2")]}}
                yield {"speak_a": {"transcript": [("a", "1"), ("b", "2"), ("a", "3")]}}
                yield {"speak_b": {"transcript": [("a", "1"), ("b", "2"), ("a", "3"), ("b", "4")]}}
                rep = await self.write_rep(
                    {
                        **initial_state,
                        "transcript": [("a", "1"), ("b", "2"), ("a", "3"), ("b", "4")],
                    }
                )
                yield {"report": {"report": rep}}

        manager = RunManager(
            factory,
            max_runs=2,
            build_graph=lambda *a, **kw: MockGraph(kw.get("write_report")),
        )

        task = await manager.start_run(
            persona_a=pa,
            persona_b=pb,
            user_id_a="u-a",
            user_id_b="u-b",
            nickname_a="A",
            nickname_b="B",
            turns=2,
            write_report=custom_write_report,
        )
        run_id = task.run_id
        await task

        assert injected_called is True
        assert build_report_called is False

        async with factory() as db:
            repo = MigrationRepository(db)
            r = await repo.get_run(run_id)
            assert r is not None
            assert r.status == "done"
            assert r.narrative_source == "custom"
            assert r.report["summary"] == "커스텀 리포트"

    asyncio.run(with_db(scenario))


def test_full_pipeline_default_report_persists_report_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """주입된 write_report가 없을 때 기본 report_tool이 호출되어 DB에 status=done, validation, narrative_source가 저장된다."""
    pa, pb = _make_personas()

    async def mock_build_report(inp: Any, agent: Any = None, **kw: Any) -> MatchingReport:
        return _make_mock_report(
            simulation_id=inp.transcript.simulation_id or "run-sim",
            headline="성공 리포트",
            highlights=[Highlight(kind="click", turn_index=0, quote="1", why="이유")],
            narrative_source="llm",
        )

    monkeypatch.setattr("app.features.simulation_migration.report_tool.build_report", mock_build_report)
    monkeypatch.setattr(
        "app.features.simulation_migration.report_tool._report_perspective_swap",
        lambda *args, **kwargs: True,
    )

    async def scenario(factory: Any) -> None:
        async with factory() as db:
            await seed_persona(db, persona_id="p-alpha", user_id="u-a", nickname="A")
            await seed_persona(db, persona_id="p-beta", user_id="u-b", nickname="B")

        class MockGraph:
            def __init__(self, write_rep: Any) -> None:
                self.write_rep = write_rep

            async def astream(self, initial_state: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
                yield {"speak_a": {"transcript": [("a", "1")]}}
                yield {"speak_b": {"transcript": [("a", "1"), ("b", "2")]}}
                yield {"speak_a": {"transcript": [("a", "1"), ("b", "2"), ("a", "3")]}}
                yield {"speak_b": {"transcript": [("a", "1"), ("b", "2"), ("a", "3"), ("b", "4")]}}
                rep = await self.write_rep(
                    {
                        **initial_state,
                        "transcript": [("a", "1"), ("b", "2"), ("a", "3"), ("b", "4")],
                    }
                )
                yield {"report": {"report": rep}}

        manager = RunManager(
            factory,
            max_runs=2,
            build_graph=lambda *a, **kw: MockGraph(kw.get("write_report")),
        )

        task = await manager.start_run(
            persona_a=pa,
            persona_b=pb,
            user_id_a="u-a",
            user_id_b="u-b",
            nickname_a="A",
            nickname_b="B",
            turns=2,
            write_report=None,  # 기본 리포트 경로
        )
        run_id = task.run_id
        await task

        async with factory() as db:
            repo = MigrationRepository(db)
            r = await repo.get_run(run_id)
            assert r is not None
            assert r.status == "done"
            assert r.narrative_source == "llm"
            assert r.report["overall"]["headline"] == "성공 리포트"
            assert r.validation == {"perspective_swap": True}

    asyncio.run(with_db(scenario))
