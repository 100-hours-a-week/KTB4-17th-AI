"""LLM (simulation).

  - ReportAgent : 대화록 + 두 페르소나 + 규칙 점수 → 서술(ReportNarrative)

점수는 여기서 만들지 않는다. 규칙으로 이미 나온 점수를 LLM에 "설명할 재료"로 준다.
LLM이 점수를 다시 매기면 규칙과 서술이 어긋나서 사용자가 헷갈린다.
ideal 영역만 예외 — 숫자끼리 비교가 안 돼서 LLM이 대화록 보고 판정한다.

_call 은 persona.agents 와 같은 시그니처. dev 플레이그라운드가 모듈 속성으로 갈아끼운다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re

from anthropic import AsyncAnthropic
from pydantic import ValidationError

from app.features.persona.schemas import SCORED, PersonaResponse

from .schemas import AREAS, DIMENSIONS_BY_AREA, RULES, Fit, ReportNarrative, Transcript

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
_client = AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LLMError(Exception):
    """호출 실패. 호출부가 잡아서 템플릿 서술로 폴백한다."""


async def _call(*, system: str, messages: list[dict], max_tokens: int, timeout: float) -> str:
    try:
        resp = await asyncio.wait_for(
            _client.messages.create(model=MODEL, max_tokens=max_tokens, system=system, messages=messages),
            timeout=timeout,
        )
    except TimeoutError as e:
        raise LLMError(f"timeout after {timeout}s") from e
    except Exception as e:
        raise LLMError(str(e)) from e

    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not text:
        raise LLMError("empty response")
    return text


async def _call_json(**kwargs) -> dict:
    text = await _call(**kwargs)
    cleaned = _FENCE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.warning("JSON parse failed: %s", cleaned[:200])
        raise LLMError(f"invalid JSON: {e}") from e


# ══ 프롬프트 재료 — schemas 정의에서 생성 ═══════════════════


def _rules_section() -> str:
    lines = []
    for area, label in AREAS.items():
        lines.append(f"[{label}]")
        for d in DIMENSIONS_BY_AREA[area]:
            rule = RULES[d]
            how = "대화록으로 판정" if rule.fit == Fit.JUDGED else f"규칙: {rule.fit.value}"
            lines.append(f"- {d} ({SCORED[d].label}) — {how}. {rule.why}")
    return "\n".join(lines)


def _persona_section(name: str, p: PersonaResponse) -> str:
    scored = ", ".join(f"{d}={p.scores.get(d, '?')}" for d in SCORED)
    head = p.narrative.headline if p.narrative else "(서술 없음)"
    return (
        f"## {name}\n"
        f"한 줄: {head}\n"
        f"점수: {scored}\n"
        f"관심사: {', '.join(p.interests) or '-'}\n"
        f"선호 데이트: {', '.join(p.date_prefer) or '-'} / 피함: {', '.join(p.date_avoid) or '-'}\n"
        f"근거 부족(LOW): {', '.join(d for d, c in p.confidence.items() if c == 'LOW') or '없음'}"
    )


def _transcript_section(t: Transcript, name_a: str, name_b: str) -> str:
    if not t.turns:
        return "(대화록 없음 — 페르소나만으로 서술)"
    names = {"a": name_a, "b": name_b}
    return "\n".join(f"[{turn.index}] {names[turn.speaker]}: {turn.text}" for turn in t.turns)


def _scores_section(area_scores: dict[str, int | None], dim_scores: dict[str, int | None]) -> str:
    lines = []
    for area, label in AREAS.items():
        s = area_scores.get(area)
        lines.append(f"- {label}: {'미정' if s is None else s}")
        for d in DIMENSIONS_BY_AREA[area]:
            ds = dim_scores.get(d)
            lines.append(f"    · {SCORED[d].label}: {'미정' if ds is None else ds}")
    return "\n".join(lines)


SYSTEM = """당신은 소개팅 매칭 리포트를 쓰는 작가입니다.
두 사람의 성향 점수(이미 계산됨)와 가상 소개팅 대화록을 읽고, 두 사람이 서로 얼마나 맞는지 설명합니다.

원칙:
- 점수를 다시 매기지 마세요. 주어진 점수를 "왜 그런지" 대화록의 장면으로 설명하세요.
- 예외: ideal_* 세 차원만 대화록을 보고 0~100 으로 판정하세요. 근거가 없으면 키를 빼세요.
- 두 사람 모두에게 보이는 글입니다. 한쪽을 깎아내리지 마세요. "A는 ~한 편이고 B는 ~한 편이라" 식으로.
- 근거 부족(LOW) 차원은 단정하지 말고 "아직 잘 모르겠지만" 톤으로.
- 한국어, "~해요" 체. 조언은 구체적으로 (예: "연락 빈도를 첫 주에 맞춰보세요").
- 하이라이트 quote 는 대화록 원문을 그대로, turn_index 와 함께.

출력은 JSON 하나만:
{
  "headline": "20자 내외 한 줄",
  "summary": "3~5문장",
  "area_comments": {"intimacy": "...", "communication": "...", "conflict": "...", "ideal": "...", "orientation": "..."},
  "highlights": [{"kind": "click|friction", "turn_index": 0, "quote": "...", "why": "..."}],
  "strengths": ["...", "..."],
  "cautions": ["...", "..."],
  "date_comment": "추천 데이트 한두 문장",
  "ideal_fit": {"ideal_warmth": 0, "ideal_vitality": 0, "ideal_status": 0}
}"""


class ReportAgent:
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
    ) -> ReportNarrative:
        """실패하면 LLMError. 호출부(service)가 템플릿으로 폴백한다."""
        user = "\n\n".join(
            [
                "# 궁합 규칙\n" + _rules_section(),
                _persona_section(name_a, persona_a),
                _persona_section(name_b, persona_b),
                "# 계산된 점수\n" + _scores_section(area_scores, dim_scores),
                "# 대화록\n" + _transcript_section(transcript, name_a, name_b),
            ]
        )
        data = await _call_json(
            system=SYSTEM,
            messages=[{"role": "user", "content": user}],
            max_tokens=1800,
            timeout=60.0,
        )
        try:
            return ReportNarrative.model_validate(data)
        except ValidationError as e:
            raise LLMError(f"invalid narrative: {e}") from e
