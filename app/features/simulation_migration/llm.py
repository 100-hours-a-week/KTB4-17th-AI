from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import get_settings
from app.features.simulation.agents import _semaphore


class LLMError(Exception):
    """시뮬레이션 마이그레이션 LLM 호출 실패 예외."""

    def __init__(
        self,
        message: str,
        *,
        reason: str = "upstream_error",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.retryable = retryable


async def complete_text(
    messages: list[dict[str, str]],
    *,
    timeout: float | None = None,
    before_request: Callable[[], Awaitable[bool]] | None = None,
    client: Any = None,
) -> str:
    """화자 발화 생성을 위한 LLM 호출 함수.

    agents._semaphore를 빌려 동시성을 제어하며, agents._call을 호출하지 않고 직접 HTTP/클라이언트를 다룬다.
    슬롯 획득 후 HTTP 직전에 before_request를 호출하여 펜싱(소유권)을 확인한다.
    Langfuse 입력 캡처를 켜지 않고 API 키를 로그에 남기지 않는다.

    Args:
        messages: LLM에 보낼 메시지 목록.
        timeout: 시도 타임아웃(초). 기본값은 settings.simulation_migration_attempt_timeout_s.
        before_request: HTTP 직전 호출할 비동기 검증 함수. False를 반환하면 소유권 상실 예외 발생.
        client: 주입할 LLM 클라이언트 (테스트용 double 지원).

    Returns:
        {"text": "..."} 형태의 원문 JSON 문자열.

    Raises:
        LLMError: timeout, busy, upstream_error, lost_ownership 등.
    """
    settings = get_settings()
    attempt_timeout = timeout if timeout is not None else settings.simulation_migration_attempt_timeout_s
    slot_timeout = settings.simulation_migration_slot_timeout_s

    sem = _semaphore()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=slot_timeout)
    except TimeoutError as e:
        raise LLMError(f"세마포어 슬롯 대기 시간 초과 ({slot_timeout}s)", reason="busy", retryable=False) from e

    try:
        if before_request is not None:
            ok = await before_request()
            if ok is False:
                raise LLMError("소유권을 상실했습니다 (fencing)", reason="lost_ownership", retryable=False)

        if client is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key or "EMPTY",
            )

        if hasattr(client, "chat") and hasattr(client.chat, "completions"):
            extra = {"response_format": {"type": "json_object"}} if settings.llm_json_mode else {}
            call_coro = client.chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                **extra,
            )
            resp = await asyncio.wait_for(call_coro, timeout=attempt_timeout)
            choice = resp.choices[0] if resp.choices else None
            text = (choice.message.content or "").strip() if choice else ""
            finish_reason = getattr(choice, "finish_reason", None)
            if finish_reason == "error":
                raise LLMError("LLM upstream error mid-generation", reason="upstream_error", retryable=True)
            return text
        elif callable(client):
            call_res = client(messages)
            if asyncio.iscoroutine(call_res) or asyncio.isfuture(call_res):
                call_res = await asyncio.wait_for(call_res, timeout=attempt_timeout)
            if isinstance(call_res, str):
                return call_res
            choice = call_res.choices[0] if hasattr(call_res, "choices") and call_res.choices else None
            return (choice.message.content or "").strip() if choice else ""
        elif hasattr(client, "complete_text"):
            call_res = client.complete_text(messages)
            if asyncio.iscoroutine(call_res) or asyncio.isfuture(call_res):
                call_res = await asyncio.wait_for(call_res, timeout=attempt_timeout)
            return str(call_res)
        else:
            raise LLMError("지원하지 않는 client 타입입니다", reason="upstream_error", retryable=False)
    except TimeoutError as e:
        raise LLMError(f"LLM 호출 시간 초과 ({attempt_timeout}s)", reason="timeout", retryable=True) from e
    except LLMError:
        raise
    except Exception as e:
        status_code = getattr(e, "status_code", None)
        retryable = status_code in (429, 500, 502, 503, 504) if status_code else False
        raise LLMError(f"LLM upstream error: {e}", reason="upstream_error", retryable=retryable) from e
    finally:
        sem.release()
