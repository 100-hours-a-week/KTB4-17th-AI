"""이미 만든 대본으로 매칭 리포트 서술을 쓴다.

dev에서 `/report/preview` 와 함께 빠지기 전에는 이 일이 `ReportAgent` 에 있었다.
한 호출 시뮬레이션은 대본과 서술을 같이 받으므로 그 경로를 되살리지 않고,
줄 단위 시뮬레이션만 여기서 서술을 따로 받는다.
"""

from __future__ import annotations

import logging

from langfuse import observe
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.guardrail import Grade, GuardrailContext, ValidationResult, effective_mode, validate
from app.core.guardrail_trace import record_guardrail
from app.core.observability import LangfuseMetadata, propagate_langfuse_metadata
from app.features.persona.schemas import SCORED, PersonaResponse
from app.features.simulation.agents import LLMError, _call_json, _rules_section, _scores_section
from app.features.simulation.report import assemble_report, score_layer
from app.features.simulation.schemas import (
    AREAS,
    GRADE_LABEL,
    DimensionFit,
    MatchingReport,
    ReportInput,
    ReportNarrative,
    Risk,
    Transcript,
    grade_of,
)

logger = logging.getLogger(__name__)

_REPORT_SHAPE = """{
  "headline": "20자 내외 한 줄",
  "summary": "3~5문장",
  "area_comments": {"intimacy": "...", "communication": "...", "conflict": "...", "ideal": "...", "orientation": "..."},
  "highlights": [{"kind": "click|friction", "turn_index": 0, "quote": "...", "why": "..."}],
  "strengths": ["...", "..."],
  "cautions": ["...", "..."],
  "date_comment": "추천 데이트 한두 문장",
  "ideal_fit": {"ideal_warmth": 0, "ideal_vitality": 0, "ideal_status": 0}
}"""

_REPORT_RULES = """- 점수를 다시 매기지 마세요. 주어진 점수를 "왜 그런지" 대화록의 장면으로 설명하세요.
- 예외: ideal_* 세 차원만 대화록을 보고 0~100 으로 판정하세요. 근거가 없으면 키를 빼거나 값을 null로 두세요.
- 두 사람 모두에게 보이는 글입니다. 한쪽을 깎아내리지 마세요. "A는 ~한 편이고 B는 ~한 편이라" 식으로.
- 근거 부족(LOW) 차원은 단정하지 말고 "아직 잘 모르겠지만" 톤으로.
- 한국어, "~해요" 체. 조언은 구체적으로 (예: "연락 빈도를 첫 주에 맞춰보세요").
- 하이라이트 quote 는 대화록 원문을 그대로, turn_index 와 함께. click(잘 통한 순간)·friction(어긋난 순간) 섞어서 3~5개."""

SYSTEM = f"""당신은 소개팅 매칭 리포트를 쓰는 작가입니다.
두 사람의 성향 점수(이미 계산됨)와 가상 소개팅 대화록을 읽고, 두 사람이 서로 얼마나 맞는지 설명합니다.

원칙:
{_REPORT_RULES}

출력은 JSON 하나만:
{_REPORT_SHAPE}"""


def _persona_section(name: str, persona: PersonaResponse) -> str:
    scored = ", ".join(f"{d}={'?' if persona.scores.get(d) is None else persona.scores[d]}" for d in SCORED)
    head = persona.narrative.headline if persona.narrative else "(서술 없음)"
    return (
        f"## {name}\n"
        f"MBTI: {persona.mbti or '-'} (참고만. 점수·대화록이 우선)\n"
        f"한 줄: {head}\n"
        f"점수: {scored}\n"
        f"관심사: {', '.join(persona.interests) or '-'}\n"
        f"선호 데이트: {', '.join(persona.date_prefer) or '-'} / 피함: {', '.join(persona.date_avoid) or '-'}\n"
        f"근거 부족(LOW): {', '.join(d for d, c in persona.confidence.items() if c == 'LOW') or '없음'}"
    )


def _transcript_section(transcript: Transcript, name_a: str, name_b: str) -> str:
    if not transcript.turns:
        return "(대화록 없음 — 페르소나만으로 서술)"
    names = {"a": name_a, "b": name_b}
    return "\n".join(f"[{turn.index}] {names[turn.speaker]}: {turn.text}" for turn in transcript.turns)


def _template_narrative(
    area_scores: dict[str, int | None], dims: list[DimensionFit], risks: list[Risk]
) -> ReportNarrative:
    good = [AREAS[a] for a, s in area_scores.items() if s is not None and grade_of(s) == "GOOD"]
    bad = [AREAS[a] for a, s in area_scores.items() if s is not None and grade_of(s) == "CAUTION"]
    headline = "·".join(good) + "이(가) 잘 맞아요" if good else "서로 알아가는 중이에요"
    parts = []
    if good:
        parts.append(f"{', '.join(good)} 영역에서 결이 비슷해요.")
    if bad:
        parts.append(f"{', '.join(bad)} 영역은 차이가 있어서 초반에 얘기해보면 좋아요.")
    if not parts:
        parts.append("크게 튀는 영역 없이 무난해요.")
    comments = {}
    for area, score in area_scores.items():
        if score is None:
            comments[area] = "대화에서 근거를 더 봐야 해요."
        else:
            comments[area] = f"{AREAS[area]} 영역은 {GRADE_LABEL[grade_of(score)]}."
    scored = [d for d in dims if d.score is not None]
    top = sorted(scored, key=lambda d: d.score or 0, reverse=True)[:2]
    low = sorted(scored, key=lambda d: d.score or 0)[:2]
    return ReportNarrative(
        headline=headline[:60],
        summary=" ".join(parts),
        area_comments=comments,
        strengths=[f"{d.label} — {d.why}" for d in top],
        cautions=[f"{d.label} — {d.why}" for d in low] + [r.caution for r in risks],
        date_comment="",
    )


def template_narrative(persona_a: PersonaResponse, persona_b: PersonaResponse) -> ReportNarrative:
    dims, area_scores, risks = score_layer(persona_a, persona_b)
    return _template_narrative(area_scores, dims, risks)


class ReportAgent:
    """대본이 이미 있을 때 리포트 서술만 받는다."""

    @observe(name="simulation-migration-report", capture_input=False, capture_output=False)
    async def write(
        self,
        *,
        persona_a: PersonaResponse,
        persona_b: PersonaResponse,
        transcript: Transcript,
        name_a: str,
        name_b: str,
        area_scores: dict[str, int | None],
        dim_scores: dict[str, int | None],
        trace_metadata: LangfuseMetadata | None = None,
        db: AsyncSession | None = None,
        user_key: str | None = None,
        session_id: str | None = None,
    ) -> ReportNarrative:
        user = "\n\n".join(
            [
                "# 궁합 규칙\n" + _rules_section(),
                _persona_section(name_a, persona_a),
                _persona_section(name_b, persona_b),
                "# 계산된 점수\n" + _scores_section(area_scores, dim_scores),
                "# 대화록\n" + _transcript_section(transcript, name_a, name_b),
            ]
        )
        with propagate_langfuse_metadata(trace_metadata):
            data = await _call_json(
                system=SYSTEM,
                messages=[{"role": "user", "content": user}],
                max_tokens=1800,
                timeout=get_settings().simulation_narrative_timeout_s,
                name="simulation-migration-report",
                metadata=trace_metadata,
            )
        try:
            narrative = ReportNarrative.model_validate(data)
        except ValidationError as e:
            raise LLMError(f"invalid narrative: {e}") from e
        mode = effective_mode(user_key)
        if mode != "off":
            text = " ".join(
                [
                    narrative.headline,
                    narrative.summary,
                    *narrative.area_comments.values(),
                    *narrative.strengths,
                    *narrative.cautions,
                    narrative.date_comment,
                ]
            )
            checked = validate(
                text,
                GuardrailContext(
                    surface="simulation_report",
                    speaker_name=name_a,
                    partner_name=name_b,
                    task="report",
                    style="report",
                    max_chars=4000,
                ),
            )
            bad = checked.grade in {Grade.RETRYABLE, Grade.BLOCK}
            result = ValidationResult(
                status="SHADOW_FAIL"
                if bad and mode == "shadow"
                else "FALLBACK"
                if bad
                else "WARN"
                if checked.grade == Grade.WARN
                else "PASS",
                grade=checked.grade,
                initial_grade=checked.grade,
                regenerated=False,
                violations=checked.violations,
                latency_ms=checked.latency_ms,
            )
            if db:
                await record_guardrail(
                    db,
                    feature="simulation",
                    operation="report",
                    session_id=session_id,
                    user_id=user_key,
                    mode=mode,
                    result=result,
                    initial_text=text,
                )
            if bad and mode == "enforce":
                self.last_validation = result
                raise LLMError("report guardrail validation failed", reason="guardrail")
            narrative.validation = result
        return narrative


async def build_report(
    inp: ReportInput,
    agent: ReportAgent | None = None,
    *,
    trace_metadata: LangfuseMetadata | None = None,
    db=None,
    user_key: str | None = None,
) -> MatchingReport:
    """서술을 받아 리포트를 조립한다. agent 가 없으면 템플릿 서술이다."""
    persona_a, persona_b = inp.persona_a, inp.persona_b
    if agent is None:
        return assemble_report(inp, template_narrative(persona_a, persona_b), "template")

    dims, area_scores, _ = score_layer(persona_a, persona_b)
    try:
        narrative = await agent.write(
            persona_a=persona_a,
            persona_b=persona_b,
            transcript=inp.transcript,
            name_a=inp.nickname_a,
            name_b=inp.nickname_b,
            area_scores=area_scores,
            dim_scores={d.dimension: d.score for d in dims},
            trace_metadata=trace_metadata,
            db=db,
            user_key=user_key,
            session_id=inp.transcript.simulation_id,
        )
    except LLMError as e:
        logger.warning("report narrative fallback: %s", e)
        report = assemble_report(inp, template_narrative(persona_a, persona_b), "template")
        report.validationResult = getattr(agent, "last_validation", None)
        return report
    report = assemble_report(inp, narrative, "llm")
    report.validationResult = narrative.validation
    return report
