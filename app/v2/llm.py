"""v2 공용 LLM JSON 호출부 — v1 app/features/persona/agents.py 의 호출 함수를 복제했다.

v2 메모리 추출·정리와 카톡 추출이 이 모듈을 쓴다.
v1 코드를 바꿔도 v2 동작이 같이 바뀌지 않도록 v1 을 import 하지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from functools import lru_cache

from langfuse import get_client
from langfuse.openai import AsyncOpenAI

from app.core import llm_retry, slm
from app.core.config import get_settings
from app.core.observability import LangfuseMetadata

logger = logging.getLogger(__name__)


@lru_cache
def _get_client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or "EMPTY",
    )


class LLMError(Exception):
    """호출 실패. 호출부가 잡아서 폴백한다."""


def _record_llm_failure(name: str, model: str, timeout: float, started: float, error: BaseException) -> None:
    """실패한 LLM 호출의 진단 정보를 로그와 현재 Langfuse span 에 남긴다.

    타임아웃으로 취소된 호출은 Langfuse 에 generation 이 남지 않아서 따로 기록한다.
    부가 정보라 기록 중 예외가 나도 호출 결과에는 영향을 주지 않는다."""
    info = {
        "llm_call": name,
        "llm_model": model,
        "llm_timeout_s": timeout,
        "llm_elapsed_s": round(time.monotonic() - started, 3),
        "llm_error_type": type(error).__name__,
    }
    status_code = getattr(error, "status_code", None)
    if status_code is not None:
        info["llm_status_code"] = status_code
    request_id = getattr(error, "request_id", None)
    if request_id:
        info["llm_request_id"] = request_id
    logger.warning("LLM call failed: %s", info)
    try:
        get_client().update_current_span(metadata=info)
    except Exception:  # noqa: BLE001 - 관측 실패가 폴백 흐름을 깨면 안 된다
        logger.debug("failed to attach llm failure info to span", exc_info=True)


async def _call(
    *,
    system: str,
    messages: list[dict],
    max_tokens: int,
    timeout: float,
    name: str = "persona-llm-call",
    metadata: LangfuseMetadata | None = None,
    use_slm: bool = True,
) -> str:
    """타임아웃·요청 한도·서버 오류·빈 응답이면 llm_retry 규칙대로 다시 보낸다. timeout 은 시도마다.

    SLM 이 설정된 호출이면 먼저 SLM 에 한 번만 보내고, 실패하면 메인 LLM 으로 넘긴다 (app/core/slm.py).
    """
    payload = [{"role": "system", "content": system}, *messages]
    route = slm.route_for(name) if use_slm else None
    if route is not None:
        try:
            return await _complete(route.client, route.model, payload, max_tokens, timeout, name, metadata, total=1)
        except LLMError as e:
            slm.record_fallback(name, e)
    return await _complete(
        _get_client(),
        get_settings().llm_model_v2,
        payload,
        max_tokens,
        timeout,
        name,
        metadata,
        total=llm_retry.attempts(),
    )


async def _complete(
    client: AsyncOpenAI,
    model: str,
    payload: list[dict],
    max_tokens: int,
    timeout: float,
    name: str,
    metadata: LangfuseMetadata | None,
    *,
    total: int,
) -> str:
    for attempt in range(1, total + 1):
        started = time.monotonic()
        try:
            async with asyncio.timeout(timeout):
                resp = await client.chat.completions.create(
                    name=name,
                    model=model,
                    messages=payload,  # type: ignore[arg-type]
                    max_tokens=max_tokens,
                    metadata=metadata,
                )
        except Exception as e:
            error = LLMError(f"timeout after {timeout}s") if isinstance(e, TimeoutError) else LLMError(str(e))
            if attempt < total and llm_retry.is_retryable(e):
                await _before_retry(name, attempt, error)
                continue
            _record_llm_failure(name, model, timeout, started, e)
            raise error from e

        text = ""
        if resp.choices:
            text = (resp.choices[0].message.content or "").strip()
        if text:
            return text
        if attempt < total:
            await _before_retry(name, attempt, LLMError("empty response"))
            continue
        raise LLMError("empty response")
    raise AssertionError("unreachable")  # 마지막 시도는 return 하거나 raise 한다


async def _before_retry(name: str, attempt: int, error: LLMError) -> None:
    logger.warning("%s attempt %d failed (%s), retrying", name, attempt, error)
    llm_retry.record_retry(name=name, attempt=attempt, reason=str(error))
    await asyncio.sleep(llm_retry.RETRY_BACKOFF_S)


async def _call_json(**kwargs) -> dict:
    text = await _call(**kwargs)
    try:
        return _parse_json(text)
    except LLMError as e:
        # SLM 이 JSON 을 깨뜨렸다면 메인 LLM 으로 한 번 더. SLM 을 안 쓰는 호출이면 그대로 실패
        name = kwargs.get("name", "")
        if slm.route_for(name) is None:
            raise
        slm.record_fallback(name, e)
        return _parse_json(await _call(**kwargs, use_slm=False))


def _parse_json(text: str) -> dict:
    # 코드펜스나 앞뒤 설명 문장이 붙어 와도 첫 { ~ 마지막 } 만 잘라 파싱한다
    start, end = text.find("{"), text.rfind("}")
    cleaned = text[start : end + 1] if start != -1 and end > start else text.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.warning("JSON parse failed: %s", cleaned[:200])
        raise LLMError(f"invalid JSON: {e}") from e
