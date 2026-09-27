"""LLM (simulation).

  - SimulationAgent : 두 페르소나 + 규칙 점수 → 대본(N턴 왕복) + 리포트 서술.  **호출 1회**
  - ReportAgent     : 대화록 + 두 페르소나 + 규칙 점수 → 서술만. /report/preview 용 (대화록을 밖에서 줄 때)

점수는 여기서 만들지 않는다. 규칙으로 이미 나온 점수를 LLM에 "설명할 재료"로 준다.
LLM이 점수를 다시 매기면 규칙과 서술이 어긋나서 사용자가 헷갈린다.
ideal 영역만 예외 — 숫자끼리 비교가 안 돼서 LLM이 대화록 보고 판정한다.

시뮬레이션이 대본과 리포트를 한 호출에 합치는 이유: 요구사항이 "LLM 1회". 대본을 먼저 쓰고 그 대본을
바로 이어서 평가하므로, 모델이 방금 쓴 장면을 turn_index 로 정확히 인용할 수 있다는 부수 이점도 있다.

_call 은 persona.agents 와 같은 시그니처. dev 플레이그라운드가 모듈 속성으로 갈아끼운다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from functools import lru_cache

from openai import AsyncOpenAI
from pydantic import ValidationError

from app.core.config import get_settings
from app.features.persona.profile import describe
from app.features.persona.schemas import SCORED, PersonaResponse

from .schemas import AREAS, DIMENSIONS_BY_AREA, RULES, Fit, ReportNarrative, ScriptOutput, Transcript

logger = logging.getLogger(__name__)


# practice.agents 와 같은 OpenAI 호환 클라이언트. 플레이그라운드는 _call 자체를 갈아끼운다.
@lru_cache
def _get_client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or "EMPTY",
    )


class LLMError(Exception):
    """호출 실패. 호출부가 잡아서 템플릿 서술로 폴백한다.

    reason 은 SimulationFailed 가 그대로 물려받아 API 503 응답에 실린다 —
    클라이언트가 "그냥 재시도"(timeout)와 "다른 조치가 필요"(그 외)를 구분할 수 있게."""

    def __init__(self, message: str, *, reason: str = "llm_error") -> None:
        super().__init__(message)
        self.reason = reason


# 동시에 진행 중인 LLM 호출 수를 settings.simulation_max_inflight 로 제한한다.
# 프로세스 하나에서만 유효 — 워커를 여러 개 띄우면 워커별로 따로 센다.
@lru_cache
def _semaphore() -> asyncio.Semaphore:
    return asyncio.Semaphore(get_settings().simulation_max_inflight)


async def _call(*, system: str, messages: list[dict], max_tokens: int, timeout: float) -> str:
    settings = get_settings()
    client = _get_client()
    payload = [{"role": "system", "content": system}, *messages]
    try:
        async with _semaphore(), asyncio.timeout(timeout):
            resp = await client.chat.completions.create(
                model=settings.llm_model,
                messages=payload,  # type: ignore[arg-type]
                max_tokens=max_tokens,
            )
    except TimeoutError as e:
        raise LLMError(f"timeout after {timeout}s", reason="timeout") from e
    except Exception as e:
        raise LLMError(str(e)) from e

    text = ""
    if resp.choices:
        text = (resp.choices[0].message.content or "").strip()
    if not text:
        raise LLMError("empty response")
    return text


async def _call_json(**kwargs) -> dict:
    text = await _call(**kwargs)
    # 코드펜스(```json, ```JSON)나 앞뒤 설명 문장이 붙어 와도 첫 { ~ 마지막 } 만 잘라 파싱한다
    start, end = text.find("{"), text.rfind("}")
    cleaned = text[start : end + 1] if start != -1 and end > start else text.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.warning("JSON parse failed: %s", cleaned[:200])
        raise LLMError(f"invalid JSON: {e}", reason="invalid_json") from e


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


# ══ 1. 시뮬레이션 — 대본 + 리포트, 호출 1회 ═════════════════

SIMULATION_SYSTEM = f"""당신은 소개팅 시뮬레이터이자 매칭 리포트 작가입니다.
두 사람의 페르소나(온보딩 대화에서 추출한 연애 성향)를 받아,
① 두 사람이 처음 만난 소개팅에서 나누는 대화를 대본으로 쓰고
② 그 대본과 미리 계산된 점수를 바탕으로 매칭 리포트를 씁니다.
둘 다 이 한 번의 응답 안에서 끝냅니다.

# ① 대본 규칙
- 메신저로 처음 대화를 시작한 상황. 인사부터 자연스럽게. a 가 먼저 말을 겁니다.
- a, b 가 번갈아 말합니다. 정확히 a→b→a→b… 순서, 요청된 턴 수(왕복) 만큼. 한 줄에 한 사람.
- 각 인물은 자기 프로필의 성향대로 말합니다. 연락 빈도가 높은 사람은 답이 빠르고 길며, 거리 두기가 높은 사람은
  자기 시간 얘기를 먼저 꺼내고, 표현이 적은 사람은 짧게 답하고, 긍정적 상호작용이 높은 사람은 "ㅎㅎ" 가 잦습니다.
- 프로필에 없는 사실(직업·나이·거주지 등)을 지어내지 마세요. 관심사·일상·데이트 취향은 프로필 것만 씁니다.
- 대화는 가볍게 시작해 서로의 주말·관심사·연애 스타일(연락, 거리감, 데이트)로 자연스럽게 흘러갑니다.
  성향이 부딪치는 지점이 있으면 억지로 감추지 말고 대화에 드러나게 하세요 — 리포트가 그 장면을 인용합니다.
- 존댓말, 편안한 구어체, 한 줄 1~3문장. 이모지 금지. "ㅎㅎ" 정도만.
- 마지막 왕복은 소개팅 끝날 때처럼 — 다음 약속을 잡거나, 아쉬운 듯 마무리.

# ② 리포트 규칙
{_REPORT_RULES}
- highlights 의 turn_index 는 위 대본에서 그 줄의 순번(0부터, a 의 첫 줄이 0)입니다.

# 출력
JSON 객체 하나만. 설명·마크다운·코드펜스 금지.
transcript 의 speaker 는 반드시 "a" 또는 "b" 리터럴만 쓰세요 — 실제 이름(위에서 알려준 닉네임)을 넣지 마세요.
{{
  "transcript": [
    {{"speaker": "a", "text": "..."}},
    {{"speaker": "b", "text": "..."}}
  ],
  "report": {_REPORT_SHAPE}
}}"""


class SimulationFailed(Exception):
    """대본을 못 받았다. 대화 없이 리포트만 만들 수는 없으므로 호출부가 503 으로 올린다.

    reason 은 API 503 응답에 그대로 실린다 — 클라이언트가 재시도 전략을 고를 수 있게
    (예: script_too_short 면 turns 를 줄여서, timeout 이면 그냥 다시)."""

    def __init__(self, message: str, *, reason: str = "unknown") -> None:
        super().__init__(message)
        self.reason = reason


def _normalize_speaker_labels(data: dict, name_a: str, name_b: str) -> dict:
    """LLM 이 화자를 "a"/"b" 대신 실제 닉네임으로 쓸 때가 있다. ScriptLine.speaker 는 Literal["a","b"]
    라 그대로면 검증에서 대본 전체가 거부된다 — normalize_script(순서 보정)가 손쓰기도 전에 막힌다.

    검증 직전에 이름 → a/b 로 되돌리고, 그래도 못 알아보는 화자는 그 줄만 버린다.
    (전체를 실패시키는 것보다, 알아볼 수 있는 줄이라도 살리는 편이 낫다)"""
    transcript = data.get("transcript")
    if not isinstance(transcript, list):
        return data

    def key(s: str) -> str:
        return s.strip().casefold()

    aliases = {"a": "a", "b": "b", key(name_a): "a", key(name_b): "b"}

    fixed = []
    for line in transcript:
        if not isinstance(line, dict):
            continue
        raw_speaker = line.get("speaker")
        mapped = aliases.get(key(str(raw_speaker))) if raw_speaker is not None else None
        if mapped is None:
            logger.warning("dropping script line with unrecognized speaker: %r", raw_speaker)
            continue
        fixed.append({**line, "speaker": mapped})

    return {**data, "transcript": fixed}


class SimulationAgent:
    async def run(
        self,
        *,
        persona_a: PersonaResponse,
        persona_b: PersonaResponse,
        name_a: str,
        name_b: str,
        turns: int,
        area_scores: dict[str, int | None],
        dim_scores: dict[str, int | None],
    ) -> ScriptOutput:
        """호출 1회. 실패하면 SimulationFailed."""
        user = "\n\n".join(
            [
                f"# 요청\n- 턴 수: {turns} 왕복 (a 발화 {turns}줄 + b 발화 {turns}줄 = 총 {turns * 2}줄)\n"
                f"- a = {name_a}, b = {name_b}. a 가 먼저 말합니다.",
                "# 두 사람의 프로필\n" + describe(f"a · {name_a}", persona_a),
                describe(f"b · {name_b}", persona_b),
                "# 궁합 규칙\n" + _rules_section(),
                "# 계산된 점수 (다시 매기지 말 것)\n" + _scores_section(area_scores, dim_scores),
            ]
        )
        try:
            data = await _call_json(
                system=SIMULATION_SYSTEM,
                messages=[{"role": "user", "content": user}],
                # 발화 한 줄 ≈ 60~90 토큰 × 2×turns + 리포트 ≈ 1500. 15턴이어도 남게.
                max_tokens=2200 + 180 * turns,
                timeout=get_settings().simulation_script_timeout_s,
            )
        except LLMError as e:
            raise SimulationFailed(str(e), reason=e.reason) from e
        data = _normalize_speaker_labels(data, name_a, name_b)
        try:
            return ScriptOutput.model_validate(data)
        except ValidationError as e:
            logger.warning("script validation failed: %s", e)
            raise SimulationFailed(f"invalid script: {e}", reason="invalid_script") from e


# ══ 2. 리포트만 — 대화록을 밖에서 줄 때 (/report/preview) ═══

SYSTEM = f"""당신은 소개팅 매칭 리포트를 쓰는 작가입니다.
두 사람의 성향 점수(이미 계산됨)와 가상 소개팅 대화록을 읽고, 두 사람이 서로 얼마나 맞는지 설명합니다.

원칙:
{_REPORT_RULES}

출력은 JSON 하나만:
{_REPORT_SHAPE}"""


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
        """실패하면 LLMError. 호출부(report.build_report)가 템플릿으로 폴백한다."""
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
            timeout=get_settings().simulation_narrative_timeout_s,
        )
        try:
            return ReportNarrative.model_validate(data)
        except ValidationError as e:
            raise LLMError(f"invalid narrative: {e}") from e
