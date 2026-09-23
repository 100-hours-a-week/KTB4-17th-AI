"""매칭 리포트 생성.

  score_layer(pa, pb)                       → (dims, area_scores, risks)   규칙 점수. LLM 없음
  assemble_report(ReportInput, narrative)   → MatchingReport               점수 + 서술을 한 장으로
  build_report(ReportInput, agent)          → MatchingReport               서술을 ReportAgent 로 받아서 위 둘

점수 층은 여기서 규칙으로 계산한다 (LLM 없음, 같은 입력이면 같은 결과).
서술 층은 밖에서 들어온다 — 시뮬레이션은 대본과 같은 LLM 호출에서 받아 assemble_report 로 바로 오고,
/report/preview 는 build_report 가 ReportAgent 를 불러 받는다. 실패하면 점수만으로 템플릿 문장을 만든다.
그래서 LLM 키가 없어도 리포트는 항상 나온다 — 프론트가 형식을 먼저 붙일 수 있게.
"""

from __future__ import annotations

import logging
from typing import Literal

from app.features.persona.schemas import CONFIDENCE_LOW, SCORED, PersonaResponse

from .agents import LLMError, ReportAgent
from .schemas import (
    AREA_WEIGHT,
    AREAS,
    DIMENSIONS_BY_AREA,
    GRADE_LABEL,
    RISK_THRESHOLD,
    RISKS,
    RULES,
    AreaReport,
    DateSuggestion,
    DimensionFit,
    Fit,
    MatchingReport,
    Overall,
    ReportConfidence,
    ReportInput,
    ReportNarrative,
    Risk,
    grade_of,
)

logger = logging.getLogger(__name__)

_CONF_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


# ══ 점수 층 ════════════════════════════════════════════════


def dimension_score(fit: Fit, a: int, b: int) -> int | None:
    if fit == Fit.SIMILAR:
        return 100 - abs(a - b)
    if fit == Fit.BOTH_HIGH:
        return (a + b) // 2
    if fit == Fit.BOTH_LOW:
        return 100 - (a + b) // 2
    return None  # JUDGED — LLM 이 채우거나 None 으로 남음


def _lower_confidence(a: str, b: str) -> str:
    return a if _CONF_ORDER.get(a, 0) <= _CONF_ORDER.get(b, 0) else b


def score_dimensions(
    pa: PersonaResponse, pb: PersonaResponse, ideal_fit: dict[str, int] | None = None
) -> list[DimensionFit]:
    ideal_fit = ideal_fit or {}
    out = []
    for d, dim in SCORED.items():
        rule = RULES[d]
        a, b = pa.scores.get(d, 50), pb.scores.get(d, 50)
        score = ideal_fit.get(d) if rule.fit == Fit.JUDGED else dimension_score(rule.fit, a, b)
        out.append(
            DimensionFit(
                dimension=d,
                label=dim.label,
                a=a,
                b=b,
                fit=rule.fit,
                score=score,
                why=rule.why,
                confidence=_lower_confidence(pa.confidence.get(d, "LOW"), pb.confidence.get(d, "LOW")),
            )
        )
    return out


def _mean(values: list[int]) -> int | None:
    return round(sum(values) / len(values)) if values else None


def score_areas(dims: list[DimensionFit]) -> dict[str, int | None]:
    by_dim = {d.dimension: d.score for d in dims}
    return {area: _mean([s for d in DIMENSIONS_BY_AREA[area] if (s := by_dim[d]) is not None]) for area in AREAS}


def triggered_risks(pa: PersonaResponse, pb: PersonaResponse) -> list[Risk]:
    """a_dim↑ + b_dim↑ 조합. 누가 a 든 상관없으니 양방향 검사."""

    def hits(p1: PersonaResponse, p2: PersonaResponse, r: Risk) -> bool:
        return p1.scores.get(r.a_dim, 50) >= RISK_THRESHOLD and p2.scores.get(r.b_dim, 50) >= RISK_THRESHOLD

    return [r for r in RISKS if hits(pa, pb, r) or hits(pb, pa, r)]


def overall_score(area_scores: dict[str, int | None], risks: list[Risk]) -> int:
    weighted = [(s, AREA_WEIGHT[a]) for a, s in area_scores.items() if s is not None]
    if not weighted:
        return 50
    base = sum(s * w for s, w in weighted) / sum(w for _, w in weighted)
    return max(0, min(100, round(base) - sum(r.penalty for r in risks)))


# ══ 서술 층 폴백 ═══════════════════════════════════════════
# LLM 이 없거나 실패했을 때. 점수만 읽고 문장을 만든다.


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
    for area, s in area_scores.items():
        if s is None:
            comments[area] = "대화에서 근거를 더 봐야 해요."
        else:
            comments[area] = f"{AREAS[area]} 영역은 {GRADE_LABEL[grade_of(s)]}."

    scored = [d for d in dims if d.score is not None]
    top = sorted(scored, key=lambda d: d.score, reverse=True)[:2]
    low = sorted(scored, key=lambda d: d.score)[:2]
    return ReportNarrative(
        headline=headline[:60],
        summary=" ".join(parts),
        area_comments=comments,
        strengths=[f"{d.label} — {d.why}" for d in top],
        cautions=[f"{d.label} — {d.why}" for d in low] + [r.caution for r in risks],
        date_comment="",
    )


# ══ 조립 ═══════════════════════════════════════════════════


def _date_suggestion(pa: PersonaResponse, pb: PersonaResponse, comment: str) -> DateSuggestion:
    prefer = [x for x in pa.date_prefer if x in set(pb.date_prefer)]
    avoid = list(dict.fromkeys([*pa.date_avoid, *pb.date_avoid]))
    return DateSuggestion(suggested=prefer, avoid=avoid, comment=comment)


def _confidence(pa: PersonaResponse, pb: PersonaResponse, dims: list[DimensionFit]) -> ReportConfidence:
    low = [d.dimension for d in dims if d.confidence == CONFIDENCE_LOW]
    accuracy = min(pa.accuracy, pb.accuracy)
    note = ""
    if accuracy < 40:
        note = "페르소나 정확도가 낮아요. 온보딩 보강 질문에 더 답하면 리포트가 정확해져요."
    elif low:
        note = f"{len(low)}개 차원은 아직 근거가 부족해서 참고만 하세요."
    return ReportConfidence(accuracy=accuracy, low_dimensions=low, note=note)


def score_layer(
    pa: PersonaResponse, pb: PersonaResponse, ideal_fit: dict[str, int] | None = None
) -> tuple[list[DimensionFit], dict[str, int | None], list[Risk]]:
    """규칙 점수 한 묶음. ideal_fit 이 없으면 ideal 차원은 None (LLM 판정 전)."""
    dims = score_dimensions(pa, pb, ideal_fit)
    return dims, score_areas(dims), triggered_risks(pa, pb)


def template_narrative(pa: PersonaResponse, pb: PersonaResponse) -> ReportNarrative:
    """LLM 없이 점수만으로. 시뮬레이션은 쓰지 않는다 (대본이 없으면 시뮬레이션 자체가 실패)."""
    dims, area_scores, risks = score_layer(pa, pb)
    return _template_narrative(area_scores, dims, risks)


def assemble_report(
    inp: ReportInput, narrative: ReportNarrative, source: Literal["llm", "template"] = "llm"
) -> MatchingReport:
    """점수 층(규칙) + 서술 층(들어온 것) → 리포트 한 장. LLM 호출 없음."""
    pa, pb = inp.persona_a, inp.persona_b

    # 규칙 점수. LLM 이 ideal 을 판정했으면 그걸 넣어 ideal 차원까지 채운다
    dims, area_scores, risks = score_layer(pa, pb, narrative.ideal_fit or None)

    total = overall_score(area_scores, risks)
    by_area = {d.dimension: d for d in dims}
    areas = []
    for area, label in AREAS.items():
        s = area_scores[area]
        g = grade_of(s) if s is not None else None
        areas.append(
            AreaReport(
                area=area,
                label=label,
                score=s,
                grade=g,
                grade_label=GRADE_LABEL[g] if g else None,
                comment=narrative.area_comments.get(area, ""),
                dimensions=[by_area[d] for d in DIMENSIONS_BY_AREA[area]],
            )
        )

    # 위험 조합 caution 은 LLM 이 안 썼어도 반드시 들어간다
    cautions = list(narrative.cautions)
    for r in risks:
        if r.caution not in cautions:
            cautions.append(r.caution)

    return MatchingReport(
        simulation_id=inp.transcript.simulation_id,
        persona_a_id=pa.persona_id,
        persona_b_id=pb.persona_id,
        overall=Overall(
            score=total,
            grade=grade_of(total),
            grade_label=GRADE_LABEL[grade_of(total)],
            headline=narrative.headline,
            summary=narrative.summary,
        ),
        areas=areas,
        highlights=narrative.highlights,
        strengths=narrative.strengths,
        cautions=cautions[:5],
        risks=[r.id for r in risks],
        date_suggestion=_date_suggestion(pa, pb, narrative.date_comment),
        confidence=_confidence(pa, pb, dims),
        narrative_source=source,
    )


async def build_report(inp: ReportInput, agent: ReportAgent | None = None) -> MatchingReport:
    """서술을 ReportAgent 로 받아서 조립. 대화록을 밖에서 줄 때(/report/preview) 쓴다."""
    pa, pb = inp.persona_a, inp.persona_b
    if agent is None:
        return assemble_report(inp, template_narrative(pa, pb), "template")

    dims, area_scores, _ = score_layer(pa, pb)
    try:
        narrative = await agent.write(
            persona_a=pa,
            persona_b=pb,
            transcript=inp.transcript,
            name_a=inp.nickname_a,
            name_b=inp.nickname_b,
            area_scores=area_scores,
            dim_scores={d.dimension: d.score for d in dims},
        )
    except LLMError as e:
        logger.warning("report narrative fallback: %s", e)
        return assemble_report(inp, template_narrative(pa, pb), "template")
    return assemble_report(inp, narrative, "llm")
