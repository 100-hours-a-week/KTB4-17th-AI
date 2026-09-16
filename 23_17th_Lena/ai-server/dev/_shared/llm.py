"""기능 공통 LLM 호출기. 각 기능의 `_call` 자리에 끼운다.

시그니처: call(system, messages, max_tokens, timeout) → str
키·프로바이더는 요청 헤더에서 ContextVar 로 들어온다 (headers 의존성).

실패는 이 모듈의 LLMError 로 던진다. 기능 코드가 자기 LLMError 를 잡아 폴백하므로,
끼울 때는 bind(feature_error_cls) 로 감싸서 그쪽 예외로 바꿔 준다.

Gemini 는 표준 라이브러리 REST. 모델은 목록이고, 무료 티어 일일 한도(PerDay 429)나
404 가 나면 다음 모델로 넘어간다. 3.x 는 thinking 을 끌 수 없어 thinkingLevel 최소 + 토큰 여유.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
from contextvars import ContextVar

from fastapi import Header

logger = logging.getLogger(__name__)


class LLMError(Exception):
    pass


api_key: ContextVar[str | None] = ContextVar("dev_api_key", default=None)
provider: ContextVar[str | None] = ContextVar("dev_provider", default=None)

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
GEMINI_MODELS = [
    m.strip()
    for m in os.environ.get(
        "GEMINI_MODEL",
        "gemini-2.5-flash-lite,gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemini-2.5-flash,gemini-3.5-flash",
    ).split(",")
    if m.strip()
]
TIMEOUT_SCALE = {"anthropic": 1.0, "gemini": float(os.environ.get("LLM_TIMEOUT_SCALE", "4.0"))}

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_exhausted: dict[str, float] = {}
_EXHAUST_TTL = 24 * 3600
_anthropic_clients: dict = {}


async def headers(
    x_api_key: str | None = Header(default=None),
    x_provider: str | None = Header(default=None),
) -> None:
    """FastAPI 의존성. async 라 엔드포인트와 같은 태스크 → ContextVar 가 그대로 보인다."""
    api_key.set(x_api_key or None)
    provider.set(x_provider or None)


def resolve() -> tuple[str, str]:
    key = api_key.get()
    p = (
        provider.get()
        or ("gemini" if (key or "").startswith("AIza") else None)
        or os.environ.get("LLM_PROVIDER")
        or "gemini"
    )
    if not key:
        key = os.environ.get("GEMINI_API_KEY" if p == "gemini" else "ANTHROPIC_API_KEY")
    if not key:
        raise LLMError(f"no API key — X-Api-Key 헤더 또는 환경변수 ({p})")
    return p, key


def gemini_model() -> str:
    now = time.monotonic()
    for m in GEMINI_MODELS:
        if now - _exhausted.get(m, -_EXHAUST_TTL) >= _EXHAUST_TTL:
            return m
    return GEMINI_MODELS[0]


def current_model() -> str:
    p, _ = resolve()
    return gemini_model() if p == "gemini" else ANTHROPIC_MODEL


def models() -> dict:
    return {"anthropic": ANTHROPIC_MODEL, "gemini": gemini_model(), "gemini_chain": GEMINI_MODELS}


# ── Anthropic ────────────────────────────────────────────


async def _anthropic(key: str, system: str, messages: list[dict], max_tokens: int) -> str:
    from anthropic import AsyncAnthropic

    if key not in _anthropic_clients:
        _anthropic_clients[key] = AsyncAnthropic(api_key=key)
    resp = await _anthropic_clients[key].messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
    )
    return "".join(b.text for b in resp.content if b.type == "text")


# ── Gemini ───────────────────────────────────────────────


class _SkipModel(LLMError):
    """일일 한도 · 없는 모델. 다음 모델로."""


def _gemini_body(model: str, system: str, messages: list[dict], max_tokens: int) -> dict:
    gen: dict = {"maxOutputTokens": max_tokens}
    if model == "gemini-2.5-flash" or model.startswith("gemini-2.5-pro"):
        gen["thinkingConfig"] = {"thinkingBudget": 0}
    elif model.startswith("gemini-3"):
        gen["thinkingConfig"] = {"thinkingLevel": "minimal" if "lite" in model else "low"}
        gen["maxOutputTokens"] = max_tokens + 1024
    return {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [
            {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
            for m in messages
        ],
        "generationConfig": gen,
    }


def _http_error(e: urllib.error.HTTPError) -> str:
    body = e.read().decode("utf-8", "replace")
    try:
        err = json.loads(body)["error"]
    except (json.JSONDecodeError, KeyError):
        return f"gemini HTTP {e.code}: {body[:300]}"
    if e.code != 429:
        return f"gemini HTTP {e.code}: {err.get('message', body[:300])}"
    quotas, retry = [], None
    for d in err.get("details", []):
        for v in d.get("violations", []):
            quotas.append(f"{v.get('quotaId')}={v.get('quotaValue')}")
        retry = d.get("retryDelay") or retry
    return (
        f"gemini HTTP 429 quota exceeded — {', '.join(quotas) or 'unknown'}"
        f"{f', retry after {retry}' if retry else ''} · https://ai.dev/rate-limit"
    )


def _gemini_once(model: str, key: str, system: str, messages: list[dict], max_tokens: int, timeout: float) -> str:
    req = urllib.request.Request(
        _GEMINI_URL.format(model=model),
        data=json.dumps(_gemini_body(model, system, messages, max_tokens)).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        msg = _http_error(e)
        if (e.code == 429 and "PerDay" in msg) or e.code == 404:
            raise _SkipModel(msg) from e
        raise LLMError(f"[{model}] {msg}") from e
    try:
        cand = data["candidates"][0]
        parts = cand.get("content", {}).get("parts", [])
    except (KeyError, IndexError) as e:
        raise LLMError(f"gemini: no candidates ({data.get('promptFeedback')})") from e
    text = "".join(p.get("text", "") for p in parts)
    if not text and cand.get("finishReason") not in (None, "STOP"):
        raise LLMError(f"gemini: finishReason={cand.get('finishReason')}")
    return text


def _gemini_sync(key: str, system: str, messages: list[dict], max_tokens: int, timeout: float) -> str:
    tried: list[str] = []
    while True:
        model = gemini_model()
        if model in tried:
            raise LLMError(f"gemini: 모든 모델 소진/없음 {tried}")
        tried.append(model)
        try:
            return _gemini_once(model, key, system, messages, max_tokens, timeout)
        except _SkipModel as e:
            _exhausted[model] = time.monotonic()
            logger.warning("gemini %s 건너뜀 → 다음 모델 (%s)", model, e)


# ── 공개 ─────────────────────────────────────────────────


async def call(*, system: str, messages: list[dict], max_tokens: int, timeout: float) -> str:
    p, key = resolve()
    timeout = timeout * TIMEOUT_SCALE[p]
    started = time.monotonic()
    try:
        if p == "gemini":
            coro = asyncio.to_thread(_gemini_sync, key, system, messages, max_tokens, timeout)
        else:
            coro = _anthropic(key, system, messages, max_tokens)
        text = (await asyncio.wait_for(coro, timeout=timeout)).strip()
    except TimeoutError as e:
        raise LLMError(f"timeout after {timeout:.1f}s ({p})") from e
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(str(e)) from e
    logger.info("%s %s %.2fs max_tokens=%d", p, current_model(), time.monotonic() - started, max_tokens)
    if not text:
        raise LLMError("empty response")
    return text


def bind(error_cls: type[Exception]):
    """기능 코드의 예외 타입으로 감싼 call. `agents._call = bind(agents.LLMError)` 식으로 끼운다."""

    async def _call(*, system: str, messages: list[dict], max_tokens: int, timeout: float) -> str:
        try:
            return await call(system=system, messages=messages, max_tokens=max_tokens, timeout=timeout)
        except LLMError as e:
            raise error_cls(str(e)) from e

    return _call


async def check() -> dict:
    """키·프로바이더가 통하는지 아주 작은 호출 1번. 각 플레이그라운드의 POST /check."""
    try:
        p, _ = resolve()
        await call(
            system="Reply with exactly: ok", messages=[{"role": "user", "content": "ok?"}], max_tokens=5, timeout=10.0
        )
    except LLMError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "provider": p, "model": current_model()}
