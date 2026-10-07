"""시뮬레이션 마이그레이션 매칭 리포트 도구.

기존 build_report를 호출하여 MatchingReport를 생성하고,
범위 밖 하이라이트를 필터링하며 perspective swap 검증 결과를 부가한다.
"""

from __future__ import annotations

import logging
from typing import Any

from app.features.persona.schemas import PersonaResponse
from app.features.simulation.agents import _report_perspective_swap
from app.features.simulation.schemas import ReportInput, Transcript, Turn
from app.features.simulation_migration.narrative import ReportAgent, build_report

logger = logging.getLogger(__name__)


def _extract(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


async def write_matching_report(state: Any) -> dict[str, Any]:
    """기존 build_report를 호출해 MatchingReport를 생성하고 검증 결과를 부가한 dict를 반환한다.

    1. state에서 persona_a, persona_b, nickname_a, nickname_b, transcript, run_id를 추출.
    2. transcript를 Turn 목록으로 구성.
    3. build_report(inp, ReportAgent()) 호출. 실패 시 build_report(inp, None)로 템플릿 폴백.
    4. 범위 밖(0 <= turn_index < len(transcript)) highlight 필터링.
    5. _report_perspective_swap 검증을 수행하여 validation dict 추가.
    6. MatchingReport.model_dump(mode="json")에 narrative_source, validation을 더해 반환.
    """
    raw_pa = _extract(state, "persona_a")
    raw_pb = _extract(state, "persona_b")
    nickname_a = str(_extract(state, "nickname_a", "A"))
    nickname_b = str(_extract(state, "nickname_b", "B"))
    raw_transcript = _extract(state, "transcript", [])
    run_id = _extract(state, "run_id")

    persona_a = raw_pa if isinstance(raw_pa, PersonaResponse) else PersonaResponse.model_validate(raw_pa)
    persona_b = raw_pb if isinstance(raw_pb, PersonaResponse) else PersonaResponse.model_validate(raw_pb)

    turns: list[Turn] = []
    for i, item in enumerate(raw_transcript):
        if isinstance(item, Turn):
            turns.append(Turn(index=i, speaker=item.speaker, text=item.text))
        elif isinstance(item, (tuple, list)):
            spk = "a" if item[0] in ("a", "A") else "b"
            turns.append(Turn(index=i, speaker=spk, text=str(item[1])))
        elif isinstance(item, dict):
            spk_raw = item.get("speaker") or item.get("spk") or "a"
            spk = "a" if spk_raw in ("a", "A") else "b"
            txt = str(item.get("text") or item.get("txt") or "")
            turns.append(Turn(index=i, speaker=spk, text=txt))

    sim_id = str(run_id) if run_id is not None else None
    inp = ReportInput(
        persona_a=persona_a,
        persona_b=persona_b,
        nickname_a=nickname_a,
        nickname_b=nickname_b,
        transcript=Transcript(simulation_id=sim_id, turns=turns),
    )

    try:
        report = await build_report(inp, ReportAgent())
    except Exception as e:
        logger.warning("build_report with ReportAgent failed, falling back to template: %s", e)
        report = await build_report(inp, None)

    # 4. turn_index가 0 <= turn_index < len(transcript)가 아닌 highlight 제거
    total_turns = len(turns)
    report.highlights = [h for h in report.highlights if 0 <= h.turn_index < total_turns]

    # 5. perspective_swap 검증 (headline, summary, area comment, strength, caution, date comment)
    text_parts: list[str] = []
    if report.overall:
        if report.overall.headline:
            text_parts.append(report.overall.headline)
        if report.overall.summary:
            text_parts.append(report.overall.summary)
    if report.areas:
        for area in report.areas:
            if area.comment:
                text_parts.append(area.comment)
    if report.strengths:
        text_parts.extend(s for s in report.strengths if s)
    if report.cautions:
        text_parts.extend(c for c in report.cautions if c)
    if report.date_suggestion and report.date_suggestion.comment:
        text_parts.append(report.date_suggestion.comment)

    report_text = " ".join(text_parts)
    is_swap = _report_perspective_swap(
        report_text,
        persona_a,
        persona_b,
        nickname_a,
        nickname_b,
    )
    validation_data = {"perspective_swap": bool(is_swap)}

    # 6. dict 변환
    dump = report.model_dump(mode="json")
    dump["narrative_source"] = report.narrative_source
    dump["validation"] = validation_data
    return dump
