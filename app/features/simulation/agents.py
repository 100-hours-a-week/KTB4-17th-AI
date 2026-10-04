"""LLM (simulation).

  - SimulationAgent : 두 페르소나 + 규칙 점수 → 대본(N턴 왕복) + 리포트 서술.  **호출 1회**

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
import re
import time
from functools import lru_cache

from langfuse import get_client, observe
from langfuse.openai import AsyncOpenAI
from openai import APIStatusError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.guardrail import (
    Domain,
    Grade,
    GuardrailContext,
    ValidationResult,
    Violation,
    correction_message,
    effective_mode,
    validate,
)
from app.core.guardrail_trace import record_guardrail
from app.core.observability import LangfuseMetadata, propagate_langfuse_metadata
from app.features.persona.profile import describe
from app.features.persona.schemas import SCORED, PersonaResponse

from .schemas import AREAS, DIMENSIONS_BY_AREA, RULES, Fit, ScriptOutput

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
    """호출 실패. SimulationAgent.run 이 잡아서 재시도하거나 SimulationFailed 로 올린다.

    reason 은 SimulationFailed 가 그대로 물려받아 API 503 응답에 실린다 —
    클라이언트가 "그냥 재시도"(timeout)와 "다른 조치가 필요"(그 외)를 구분할 수 있게.

    reason: timeout · upstream_error(LLM 서버 4xx/5xx, 생성 도중 끊김) · truncated(max_tokens 에서 잘림)
            · invalid_json · llm_error(그 외)

    retryable: 같은 요청을 바로 다시 보내면 될 수 있는 실패. reason 은 API 계약이라 그대로 두고
    재시도 여부만 따로 표시한다 — 생성 도중 끊김은 upstream_error 지만 4xx/5xx 와 달리 SDK 가 재시도하지 않았다."""

    def __init__(self, message: str, *, reason: str = "llm_error", retryable: bool = False) -> None:
        super().__init__(message)
        self.reason = reason
        self.retryable = retryable


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
    if finish_reason == "error":
        # HTTP 200 이어도 공급자가 생성 도중 끊은 경우다. 반쪽 출력을 파싱하면 invalid_json 으로 잘못 분류된다 (#77)
        # OpenRouter 는 200 을 먼저 보내 버려서 끊긴 사유를 본문(choice.error, native_finish_reason)에만 싣는다.
        # 사유는 503 응답 메시지에 넣지 않고 로그·Langfuse 에만 남긴다
        detail = _provider_error_detail(resp, choice)
        logger.warning("llm %s: provider error mid-generation detail=%s tail=%r", name, detail, text[-200:])
        _record_provider_error(detail)
        raise LLMError("provider error mid-generation (finish_reason=error)", reason="upstream_error", retryable=True)
    if not text:
        raise LLMError("empty response")
    return text


def _provider_error_detail(resp, choice) -> dict:
    """끊긴 응답에 OpenRouter 가 붙여 준 사유. SDK 모델에 없는 필드라 getattr 로 읽는다."""
    error = getattr(choice, "error", None)
    if isinstance(error, str):
        error = {"message": error}
    elif error is not None and not isinstance(error, dict):
        error = {"code": getattr(error, "code", None), "message": getattr(error, "message", None)}
    error = error or {}
    message = error.get("message")
    # metadata 에는 공급자가 보낸 원문(raw)이 온다 — message 가 "Provider returned error" 뿐일 때 실제 사유는 여기 있다
    raw = error.get("metadata")
    return {
        "generation_id": getattr(resp, "id", None),
        "provider": getattr(resp, "provider", None),
        "native_finish_reason": getattr(choice, "native_finish_reason", None),
        "error_code": error.get("code"),
        "error_message": str(message)[:500] if message is not None else None,
        "error_metadata": json.dumps(raw, ensure_ascii=False, default=str)[:500] if raw is not None else None,
    }


def _record_provider_error(detail: dict) -> None:
    # 관측 실패가 재시도·503 흐름을 바꾸면 안 된다
    try:
        get_client().update_current_span(metadata={"provider_error": detail})
    except Exception:
        logger.debug("failed to record provider error on langfuse span", exc_info=True)


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
        db: AsyncSession | None = None,
        user_key: str | None = None,
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
        mode = effective_mode(user_key)
        retry_notice = ""
        first_failure: ValidationResult | None = None
        first_text = ""
        # 화자 뒤바뀜으로 다시 받을 때 1차 대본을 둔다. 재생성이 실패하면 503 대신 이걸 쓴다 (#77)
        mixed_first: ScriptOutput | None = None
        with propagate_langfuse_metadata(trace_metadata):
            for attempt in (1, 2):
                try:
                    data = await _call_json(
                        system=SIMULATION_SYSTEM,
                        messages=[{"role": "user", "content": user + retry_notice}],
                        max_tokens=max_tokens,
                        timeout=total if attempt == 1 else deadline - time.monotonic(),
                        name="simulation-run",
                        metadata=trace_metadata,
                    )
                    script = _validate_script(data, name_a, name_b)
                except (LLMError, SimulationFailed) as e:
                    reason = e.reason
                    # 출력이 깨지거나 잘린 건 운이다 — 같은 요청을 한 번만 더. 전체 시간은 처음 timeout 안에서.
                    # 남은 시간이 1/3 도 안 되면 재시도해도 또 타임아웃이라 바로 실패시킨다.
                    # 생성 도중 끊김(retryable)은 운영에서 몇 초 뒤 같은 요청이 성공했다 — 같은 규칙으로 한 번만 더.
                    remaining = deadline - time.monotonic()
                    retryable = reason in _RETRYABLE or getattr(e, "retryable", False)
                    if attempt == 1 and retryable and remaining > total / 3:
                        logger.warning("simulation-run retry: reason=%s remaining=%.1fs", reason, remaining)
                        if reason == "truncated":
                            max_tokens = int(max_tokens * 1.5)
                        continue
                    if mixed_first is None:
                        if isinstance(e, SimulationFailed):
                            raise
                        raise SimulationFailed(str(e), reason=reason) from e
                    logger.warning("simulation-run regeneration failed (reason=%s) — using first script", reason)
                    script = mixed_first
                else:
                    mixed = _self_addressed_lines(script, name_a, name_b)
                    remaining = deadline - time.monotonic()
                    if mixed and attempt == 1 and remaining > total / 3:
                        # 화자가 섞인 대본 — 리포트도 뒤바뀐 대본을 인용하게 된다. 한 번만 다시 받는다
                        logger.warning(
                            "simulation-run retry: reason=speaker_mixup lines=%s remaining=%.1fs", mixed, remaining
                        )
                        mixed_first = script
                        continue
                if mode != "off":
                    checked, checked_text = _validate_output(script, persona_a, persona_b, name_a, name_b)
                    if checked.grade in {Grade.RETRYABLE, Grade.BLOCK}:
                        if mode == "enforce":
                            if attempt == 1 and remaining > total / 3:
                                first_failure, first_text = checked, checked_text
                                retry_notice = "\n\n" + correction_message(checked)
                                continue
                            failed = checked.model_copy(
                                update={
                                    "status": "FALLBACK",
                                    "regenerated": attempt == 2,
                                    "initial_grade": first_failure.initial_grade
                                    if first_failure
                                    else checked.initial_grade,
                                    "violations": first_failure.violations if first_failure else checked.violations,
                                }
                            )
                            if db:
                                await record_guardrail(
                                    db,
                                    feature="simulation",
                                    operation="run",
                                    session_id=None,
                                    user_id=user_key,
                                    mode=mode,
                                    result=failed,
                                    initial_text=first_text or checked_text,
                                )
                                try:
                                    await db.commit()
                                except Exception:
                                    logger.warning("guardrail failure trace commit failed", exc_info=True)
                            raise SimulationFailed("guardrail validation failed", reason="guardrail")
                        checked.status = "SHADOW_FAIL"
                    elif first_failure:
                        checked = checked.model_copy(
                            update={
                                "status": "REGENERATED",
                                "initial_grade": first_failure.initial_grade,
                                "regenerated": True,
                                "violations": first_failure.violations,
                                "latency_ms": checked.latency_ms + first_failure.latency_ms,
                            }
                        )
                    if db:
                        await record_guardrail(
                            db,
                            feature="simulation",
                            operation="run",
                            session_id=None,
                            user_id=user_key,
                            mode=mode,
                            result=checked,
                            initial_text=first_text or checked_text,
                        )
                    script.validation = checked
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
    """자기 닉네임에 '님'을 붙여 부르는 줄 — "지수님은요?"를 지수가 말하면 화자가 섞였다는 뚜렷한 신호.

    이름 앞에 글자가 붙어 있으면 다른 이름의 일부다 — a=셰일이 b=내가진짜셰일을 "내가진짜셰일님"이라 부른 걸
    "셰일님"으로 잘못 읽어 정상 대본을 버린 적이 있다 (#77)."""

    def calls_self(name: str) -> re.Pattern[str]:
        return re.compile(rf"(?<![가-힣A-Za-z0-9]){re.escape(name)}님")

    own = {"a": calls_self(name_a), "b": calls_self(name_b)}
    return [i for i, line in enumerate(script.transcript) if own[line.speaker].search(line.text)]


def _report_perspective_swap(text: str, pa: PersonaResponse, pb: PersonaResponse, name_a: str, name_b: str) -> bool:
    """Detect an exclusive profile phrase attributed to the other named person."""
    attrs_a = set([*pa.interests, *pa.routine, *pa.date_prefer])
    attrs_b = set([*pb.interests, *pb.routine, *pb.date_prefer])
    for owner_attrs, other_attrs, owner, other in (
        (attrs_a, attrs_b, name_a, name_b),
        (attrs_b, attrs_a, name_b, name_a),
    ):
        for attr in owner_attrs - other_attrs:
            if len(attr) < 4:
                continue
            for match in re.finditer(re.escape(other), text):
                after = text[match.end() : match.end() + 40]
                pos = after.find(attr)
                if pos >= 0 and owner not in after[:pos]:
                    return True
    return False


def _validate_output(
    script: ScriptOutput, pa: PersonaResponse, pb: PersonaResponse, name_a: str, name_b: str
) -> tuple[ValidationResult, str]:
    own = {
        "a": (pa, pb, name_a, name_b),
        "b": (pb, pa, name_b, name_a),
    }
    checks = []
    for line in script.transcript:
        speaker, partner, speaker_name, partner_name = own[line.speaker]
        own_attrs = [*speaker.interests, *speaker.routine, *speaker.date_prefer]
        other_attrs = [
            item for item in [*partner.interests, *partner.routine, *partner.date_prefer] if item not in own_attrs
        ]
        checks.append(
            validate(
                line.text,
                GuardrailContext(
                    surface="simulation_line",
                    speaker_name=speaker_name,
                    partner_name=partner_name,
                    speaker_attributes=own_attrs,
                    partner_attributes=other_attrs,
                    task="script_line",
                    max_chars=300,
                ),
            )
        )
    report = script.report
    report_text = " ".join(
        [
            report.headline,
            report.summary,
            *report.area_comments.values(),
            *report.strengths,
            *report.cautions,
            report.date_comment,
        ]
    )
    checks.append(
        validate(
            report_text,
            GuardrailContext(
                surface="simulation_report",
                speaker_name=name_a,
                partner_name=name_b,
                task="report",
                style="report",
                max_chars=4000,
            ),
        )
    )
    violations = [v for check in checks for v in check.violations]
    grade = max(
        (v.severity for v in violations),
        default=Grade.PASS,
        key=lambda g: {Grade.PASS: 0, Grade.WARN: 1, Grade.RETRYABLE: 2, Grade.BLOCK: 3}[g],
    )
    # 이름 창 검사는 엔진의 validate 결과와 독립적으로 판단한다.
    if grade in {Grade.PASS, Grade.WARN} and _report_perspective_swap(report_text, pa, pb, name_a, name_b):
        grade = Grade.RETRYABLE
        violations = [
            Violation(
                domain=Domain.PERSPECTIVE,
                rule_id="RULE-PERSPECTIVE-SWAP",
                description="리포트에서 상대의 프로필 속성을 다른 사람에게 귀속했습니다.",
                severity=Grade.RETRYABLE,
            )
        ]
    result = ValidationResult(
        status="WARN" if grade == Grade.WARN else "PASS",
        grade=grade,
        initial_grade=grade,
        regenerated=False,
        violations=violations,
        latency_ms=sum(check.latency_ms for check in checks),
    )
    full_text = "\n".join([*(line.text for line in script.transcript), report_text])
    return result, full_text
