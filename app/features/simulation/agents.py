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
import time
from functools import lru_cache

from langfuse import observe
from langfuse.openai import AsyncOpenAI
from openai import APIStatusError
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.observability import LangfuseMetadata, propagate_langfuse_metadata
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
    클라이언트가 "그냥 재시도"(timeout)와 "다른 조치가 필요"(그 외)를 구분할 수 있게.

    reason: timeout · upstream_error(LLM 서버 4xx/5xx) · truncated(max_tokens 에서 잘림)
            · invalid_json · llm_error(그 외)"""

    def __init__(self, message: str, *, reason: str = "llm_error") -> None:
        super().__init__(message)
        self.reason = reason


# 동시에 진행 중인 LLM 호출 수를 settings.simulation_max_inflight 로 제한한다.
# 프로세스 하나에서만 유효 — 워커를 여러 개 띄우면 워커별로 따로 센다.
@lru_cache
def _semaphore() -> asyncio.Semaphore:
    return asyncio.Semaphore(get_settings().simulation_max_inflight)


async def _call(
    *,
    system: str,
    messages: list[dict],
    max_tokens: int,
    timeout: float,
    name: str = "simulation-llm-call",
    metadata: LangfuseMetadata | None = None,
) -> str:
    settings = get_settings()
    client = _get_client()
    payload = [{"role": "system", "content": system}, *messages]
    # 두 호출 모두 JSON 만 받는다. 지원하는 모델이면 따옴표·쉼표 같은 문법 오류가 크게 준다
    extra = {"response_format": {"type": "json_object"}} if settings.llm_json_mode else {}
    started = time.monotonic()
    try:
        async with _semaphore(), asyncio.timeout(timeout):
            resp = await client.chat.completions.create(
                name=name,
                model=settings.llm_model,
                messages=payload,  # type: ignore[arg-type]
                max_tokens=max_tokens,
                metadata=metadata,
                **extra,
            )
    except TimeoutError as e:
        logger.warning("llm %s: timeout after %.1fs", name, time.monotonic() - started)
        raise LLMError(f"timeout after {timeout}s", reason="timeout") from e
    except APIStatusError as e:
        # OpenRouter 는 뒤의 모델 공급자가 죽으면 502 를 준다. SDK 재시도까지 다 실패한 경우다
        logger.warning("llm %s: upstream status=%s after %.1fs: %s", name, e.status_code, time.monotonic() - started, e)
        raise LLMError(f"upstream error {e.status_code}: {e}", reason="upstream_error") from e
    except Exception as e:
        logger.warning("llm %s: %s after %.1fs: %s", name, type(e).__name__, time.monotonic() - started, e)
        raise LLMError(str(e)) from e

    choice = resp.choices[0] if resp.choices else None
    text = (choice.message.content or "").strip() if choice else ""
    finish_reason = getattr(choice, "finish_reason", None)
    usage = getattr(resp, "usage", None)
    logger.info(
        "llm %s: finish_reason=%s completion_tokens=%s/%d chars=%d elapsed=%.1fs",
        name,
        finish_reason,
        getattr(usage, "completion_tokens", None),
        max_tokens,
        len(text),
        time.monotonic() - started,
    )
    if finish_reason == "length":
        # 잘린 JSON 은 파싱이 안 된다. invalid_json 과 구분해야 max_tokens 를 늘릴지 판단할 수 있다
        logger.warning("llm %s: output truncated at max_tokens=%d, tail=%r", name, max_tokens, text[-200:])
        raise LLMError(f"output truncated at max_tokens={max_tokens}", reason="truncated")
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
        # 앞만 찍으면 원인을 못 본다. 깨진 위치 주변과 끝부분을 같이 남긴다
        logger.warning(
            "JSON parse failed: %s (chars=%d) near=%r head=%r tail=%r",
            e,
            len(cleaned),
            cleaned[max(0, e.pos - 100) : e.pos + 100],
            cleaned[:200],
            cleaned[-200:],
        )
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
    scored = ", ".join(f"{d}={'?' if p.scores.get(d) is None else p.scores[d]}" for d in SCORED)
    head = p.narrative.headline if p.narrative else "(서술 없음)"
    return (
        f"## {name}\n"
        f"MBTI: {p.mbti or '-'} (참고만. 점수·대화록이 우선)\n"
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
- **각 인물은 자기 프로필에 있는 것만 자기 얘기로 말합니다.** 상대 프로필의 관심사·일상·이상형·성향을 자기 것처럼 말하지 마세요.
  (예: b 프로필에만 "얼굴을 먼저 본다"가 있으면 a 는 그 말을 하지 않습니다)
- 상대를 부를 땐 **상대의** 닉네임을 씁니다. a 는 b 를, b 는 a 를 부르고, 자기 닉네임으로 상대를 부르지 않습니다.
- "~라고 하셨잖아요"처럼 인용하는 건 **이 대본에서 상대가 실제로 한 줄**만. 프로필의 설명·특징 문장은 상대가 한 말이 아닙니다.
- 대화는 가볍게 시작해 서로의 주말·관심사·연애 스타일(연락, 거리감, 데이트)로 자연스럽게 흘러갑니다.
  성향이 부딪치는 지점이 있으면 억지로 감추지 말고 대화에 드러나게 하세요 — 리포트가 그 장면을 인용합니다.
- 존댓말, 편안한 구어체, 한 줄 1~3문장. 이모지 금지. "ㅎㅎ" 정도만.
- 마지막 왕복은 소개팅 끝날 때처럼 — 다음 약속을 잡거나, 아쉬운 듯 마무리.

# ② 리포트 규칙
{_REPORT_RULES}
- highlights 의 turn_index 는 위 대본에서 그 줄의 순번(0부터, a 의 첫 줄이 0)입니다.
- 누가 어떤 성향·이상형인지는 **프로필 기준**으로 씁니다. a 의 특성을 b 의 것으로, b 의 특성을 a 의 것으로 바꿔 쓰지 마세요.

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


# 한 번 더 부르면 나을 수 있는 실패. timeout·upstream_error 는 SDK 재시도/클라이언트 재시도에 맡긴다
_RETRYABLE = {"invalid_json", "truncated"}


class SimulationAgent:
    @observe(name="simulation-run-workflow", capture_input=False, capture_output=False)
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
        trace_metadata: LangfuseMetadata | None = None,
    ) -> ScriptOutput:
        """호출 1회. 실패하면 SimulationFailed."""
        user = "\n\n".join(
            [
                f"# 요청\n- 턴 수: {turns} 왕복 (a 발화 {turns}줄 + b 발화 {turns}줄 = 총 {turns * 2}줄)\n"
                f"- a = {name_a}, b = {name_b}. a 가 먼저 말합니다.",
                "# 두 사람의 프로필 (각 블록은 그 사람만의 것 — 서로 섞지 말 것)\n"
                + describe(f"a · {name_a} (a 의 대사에만 반영)", persona_a),
                describe(f"b · {name_b} (b 의 대사에만 반영)", persona_b),
                "# 궁합 규칙\n" + _rules_section(),
                "# 계산된 점수 (다시 매기지 말 것)\n" + _scores_section(area_scores, dim_scores),
            ]
        )
        # 한국어는 토큰을 많이 먹는다. 발화 한 줄 ≈ 100~150 토큰 × 2×turns + 리포트 ≈ 2000.
        # 예전 값(2200 + 180×turns)에서 10턴 출력이 잘려 invalid_json 503 이 실제로 났다.
        max_tokens = 3000 + 300 * turns
        total = get_settings().simulation_script_timeout_s
        deadline = time.monotonic() + total
        with propagate_langfuse_metadata(trace_metadata):
            for attempt in (1, 2):
                try:
                    data = await _call_json(
                        system=SIMULATION_SYSTEM,
                        messages=[{"role": "user", "content": user}],
                        max_tokens=max_tokens,
                        timeout=total if attempt == 1 else deadline - time.monotonic(),
                        name="simulation-run",
                        metadata=trace_metadata,
                    )
                except LLMError as e:
                    # 출력이 깨지거나 잘린 건 운이다 — 같은 요청을 한 번만 더. 전체 시간은 처음 timeout 안에서.
                    # 남은 시간이 1/3 도 안 되면 재시도해도 또 타임아웃이라 바로 실패시킨다.
                    remaining = deadline - time.monotonic()
                    if attempt == 1 and e.reason in _RETRYABLE and remaining > total / 3:
                        logger.warning("simulation-run retry: reason=%s remaining=%.1fs", e.reason, remaining)
                        if e.reason == "truncated":
                            max_tokens = int(max_tokens * 1.5)
                        continue
                    raise SimulationFailed(str(e), reason=e.reason) from e

                script = _validate_script(data, name_a, name_b)
                mixed = _self_addressed_lines(script, name_a, name_b)
                remaining = deadline - time.monotonic()
                if mixed and attempt == 1 and remaining > total / 3:
                    # 화자가 섞인 대본 — 리포트도 뒤바뀐 대본을 인용하게 된다. 한 번만 다시 받는다
                    logger.warning(
                        "simulation-run retry: reason=speaker_mixup lines=%s remaining=%.1fs", mixed, remaining
                    )
                    continue
                return script
        raise AssertionError("unreachable")  # 두 번째 시도는 반드시 return 또는 raise


def _validate_script(data: dict, name_a: str, name_b: str) -> ScriptOutput:
    data = _normalize_speaker_labels(data, name_a, name_b)
    try:
        return ScriptOutput.model_validate(data)
    except ValidationError as e:
        logger.warning("script validation failed: %s", e)
        raise SimulationFailed(f"invalid script: {e}", reason="invalid_script") from e


def _self_addressed_lines(script: ScriptOutput, name_a: str, name_b: str) -> list[int]:
    """자기 닉네임에 '님'을 붙여 부르는 줄 — "지수님은요?"를 지수가 말하면 화자가 섞였다는 뚜렷한 신호."""
    own = {"a": f"{name_a}님", "b": f"{name_b}님"}
    return [i for i, line in enumerate(script.transcript) if own[line.speaker] in line.text]


# ══ 2. 리포트만 — 대화록을 밖에서 줄 때 (/report/preview) ═══

SYSTEM = f"""당신은 소개팅 매칭 리포트를 쓰는 작가입니다.
두 사람의 성향 점수(이미 계산됨)와 가상 소개팅 대화록을 읽고, 두 사람이 서로 얼마나 맞는지 설명합니다.

원칙:
{_REPORT_RULES}

출력은 JSON 하나만:
{_REPORT_SHAPE}"""


class ReportAgent:
    @observe(name="simulation-report-preview-workflow", capture_input=False, capture_output=False)
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
        with propagate_langfuse_metadata(trace_metadata):
            data = await _call_json(
                system=SYSTEM,
                messages=[{"role": "user", "content": user}],
                max_tokens=1800,
                timeout=get_settings().simulation_narrative_timeout_s,
                name="simulation-report-preview",
                metadata=trace_metadata,
            )
        try:
            return ReportNarrative.model_validate(data)
        except ValidationError as e:
            raise LLMError(f"invalid narrative: {e}") from e
