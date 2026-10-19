"""LLM 도구 호출 래퍼 — schemas.LLMCaller 의 실제 구현.

settings.simulation_v2_use_tools 가 켜져 있으면 OpenAI 호환 tools(function calling)로 받고,
꺼져 있으면 같은 도구 스키마를 프롬프트에 적어 json_object 로 받는다 (모델이 tools 를 지원하지 않을 때).
LLM 은 출력 형식만 도구로 받는다. 다음 단계는 graph.py 의 코드가 정한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from openai import APIStatusError

from app.core.config import get_settings
from app.features.simulation.agents import _get_client, _semaphore

from .schemas import LLMResult, ToolCall

logger = logging.getLogger(__name__)


class SimLLMError(Exception):
    """reason: timeout | upstream_error | truncated | invalid_json | no_tool_call | llm_error"""

    def __init__(self, message: str, *, reason: str = "llm_error") -> None:
        super().__init__(message)
        self.reason = reason


def _parse_args(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            value = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _tool_calls(choice: Any, name: str) -> list[ToolCall]:
    """응답의 tool_calls 중 인자를 파싱할 수 있는 것만. 똑같은 호출이 반복되면 하나만 남긴다."""
    calls: list[ToolCall] = []
    seen: set[str] = set()
    for tc in getattr(choice.message, "tool_calls", None) or []:
        fn = getattr(tc, "function", None)
        args = _parse_args(getattr(fn, "arguments", None)) if fn is not None else None
        if fn is None or args is None:
            logger.warning("llm %s: skipping unparsable tool call", name)
            continue
        key = fn.name + json.dumps(args, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        calls.append(ToolCall(name=fn.name, arguments=args))
    return calls


def _json_mode_system(system: str, tools: list[dict[str, Any]]) -> str:
    specs = [
        {
            "name": t["function"]["name"],
            "description": t["function"].get("description", ""),
            "parameters": t["function"].get("parameters", {}),
        }
        for t in tools
    ]
    return (
        f"{system}\n\n# 출력 형식\n"
        "아래 도구를 호출하는 대신 JSON 객체 하나로만 답하세요. 설명·마크다운·코드펜스 금지.\n"
        '{"calls": [{"name": "<도구 이름>", "arguments": {...}}]}\n'
        "여러 번 호출하려면 calls 배열에 여러 개를 넣으세요.\n"
        f"# 도구\n{json.dumps(specs, ensure_ascii=False)}"
    )


def _calls_from_json_text(text: str, tools: list[dict[str, Any]]) -> list[ToolCall]:
    data = _parse_args(text)
    if data is None:
        raise SimLLMError("invalid JSON in json_object fallback", reason="invalid_json")
    raw_calls = data.get("calls")
    if not isinstance(raw_calls, list):
        # 감싸개 없이 한 번의 호출만 보낸 경우 ({"name":..,"arguments":..}) 또는 인자 객체만 보낸 경우.
        # 실측: 리포트에서 {"headline":..} 처럼 인자만 바로 보냈다. 도구가 하나면 그 도구의 인자로 본다
        if isinstance(data.get("name"), str) and isinstance(data.get("arguments"), dict | str):
            raw_calls = [data]
        elif len(tools) == 1:
            return [ToolCall(name=tools[0]["function"]["name"], arguments=data)]
        else:
            raise SimLLMError("json_object fallback without calls", reason="no_tool_call")
    calls = []
    for c in raw_calls:
        if isinstance(c, dict) and isinstance(c.get("name"), str):
            args = _parse_args(c.get("arguments", {}))
            if args is not None:
                calls.append(ToolCall(name=c["name"], arguments=args))
    return calls


async def call_tools(
    *,
    system: str,
    user: str,
    tools: list[dict[str, Any]],
    max_tokens: int,
    timeout: float,
    name: str,
    model: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> LLMResult:
    settings = get_settings()
    use_tools = settings.simulation_v2_use_tools
    # SDK 기본 재시도(2회)는 호출 예산 밖에서 HTTP 요청을 늘린다. 1회로 묶는다
    client = _get_client().with_options(max_retries=1)
    if use_tools:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        # parallel_tool_calls 는 공급자마다 지원이 달라 보내지 않는다 (OpenAI 계열 기본값이 병렬 허용)
        extra: dict[str, Any] = {"tools": tools, "tool_choice": "required"}
    else:
        messages = [{"role": "system", "content": _json_mode_system(system, tools)}, {"role": "user", "content": user}]
        extra = {"response_format": {"type": "json_object"}}

    started = time.monotonic()
    try:
        async with _semaphore(), asyncio.timeout(max(timeout, 0.1)):
            resp = await client.chat.completions.create(
                name=name,
                model=model or settings.llm_model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=max_tokens,
                metadata=metadata,
                **extra,
            )
    except TimeoutError as e:
        logger.warning("llm %s: timeout after %.1fs", name, time.monotonic() - started)
        raise SimLLMError(f"timeout after {timeout:.1f}s", reason="timeout") from e
    except APIStatusError as e:
        logger.warning("llm %s: upstream status=%s", name, e.status_code)
        raise SimLLMError(f"upstream error {e.status_code}", reason="upstream_error") from e
    except Exception as e:
        logger.warning("llm %s: %s: %s", name, type(e).__name__, e)
        raise SimLLMError(str(e)) from e

    choice = resp.choices[0] if resp.choices else None
    finish_reason = getattr(choice, "finish_reason", None)
    usage = getattr(resp, "usage", None)
    logger.info(
        "llm %s: finish_reason=%s completion_tokens=%s/%d elapsed=%.1fs",
        name,
        finish_reason,
        getattr(usage, "completion_tokens", None),
        max_tokens,
        time.monotonic() - started,
    )
    if choice is None:
        raise SimLLMError("empty response", reason="llm_error")
    if finish_reason == "length":
        raise SimLLMError(f"output truncated at max_tokens={max_tokens}", reason="truncated")
    if finish_reason == "error":
        # 공급자가 도구 호출 도중 끊어도(MALFORMED_FUNCTION_CALL 등) 이미 온전히 받은 호출이 있으면 살린다
        salvaged = _tool_calls(choice, name) if use_tools else []
        if not salvaged:
            raise SimLLMError("provider error mid-generation", reason="upstream_error")
        logger.warning(
            "llm %s: provider error (native=%s) — salvaged %d tool calls",
            name,
            getattr(choice, "native_finish_reason", None),
            len(salvaged),
        )
        return LLMResult(
            calls=salvaged,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
        )

    if use_tools:
        if not (getattr(choice.message, "tool_calls", None) or []):
            raise SimLLMError("no tool call in response", reason="no_tool_call")
        calls = _tool_calls(choice, name)
        if not calls:
            raise SimLLMError("all tool calls unparsable", reason="invalid_json")
    else:
        calls = _calls_from_json_text((choice.message.content or "").strip(), tools)

    return LLMResult(
        calls=calls,
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
    )
