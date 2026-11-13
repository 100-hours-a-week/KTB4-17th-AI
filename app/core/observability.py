"""Langfuse 관측 메타데이터의 공통 규칙.

기능 코드가 Langfuse 예약 필드를 직접 조립하지 않도록 한곳에서 관리한다.
프롬프트와 응답은 OpenAI 래퍼가 기록하고, 이 모듈은 호출을 사용자·세션·기능별로
찾을 수 있게 만드는 식별 정보와 실제 호출·최종 결과의 모델 출처를 다룬다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from uuid import uuid4

import openai
from langfuse import get_client, propagate_attributes
from pydantic import ValidationError

logger = logging.getLogger(__name__)

type MetadataScalar = str | int | float | bool
type LangfuseMetadata = dict[str, MetadataScalar | list[str]]

_LANGFUSE_RESERVED_KEYS = {
    "langfuse_user_id",
    "langfuse_session_id",
    "langfuse_tags",
}


@dataclass
class ModelSelection:
    operation: str
    operation_id: str
    task: asyncio.Task | None
    slm_attempted: bool = False
    slm_skip_reason: str | None = None
    fallback_reason: str | None = None
    requested_model: str | None = None
    response_model: str | None = None
    response_source: str | None = None
    final_model: str | None = None
    final_source: str | None = None
    failure_reason: str | None = None
    rule_reason: str | None = None

    def metadata(self) -> dict:
        return {
            "model_operation": self.operation,
            "model_operation_id": self.operation_id,
            "slm_attempted": self.slm_attempted,
            "slm_skip_reason": self.slm_skip_reason,
            "fallback_reason": self.fallback_reason,
            "requested_model": self.requested_model,
            "response_model": self.response_model,
            "final_model": self.final_model,
            "final_source": self.final_source,
            "failure_reason": self.failure_reason,
            "rule_reason": self.rule_reason,
        }


_selection: ContextVar[ModelSelection | None] = ContextVar("model_selection", default=None)


def current_model_selection() -> ModelSelection | None:
    selection = _selection.get()
    # create_task copies context, but must not share a parent's mutable result state.
    if selection is not None and selection.task is asyncio.current_task():
        return selection
    return None


def update_langfuse_span(**kwargs) -> None:
    try:
        get_client().update_current_span(**kwargs)
    except Exception:
        logger.debug("failed to update Langfuse span", exc_info=True)


def _model_event(name: str, **attributes) -> None:
    selection = current_model_selection()
    if selection is None:
        return
    metadata = {**selection.metadata(), **attributes}
    _write_model_event(name, metadata)


def _write_model_event(name: str, metadata: dict) -> None:
    # SDK v4 drops None attributes during merges. Replace one serialized object
    # so a later failed attempt cannot retain a previous response/final model.
    update_langfuse_span(metadata={"model_selection": metadata})
    try:
        get_client().create_event(name=name, metadata=metadata)
    except Exception:
        logger.debug("failed to record model event", exc_info=True)


def model_failure_reason(error: BaseException) -> str:
    """Stable codes only: exception messages can contain prompts, URLs or credentials."""
    cause = error
    while cause.__cause__ is not None:
        cause = cause.__cause__
    if isinstance(cause, ValidationError):
        return "schema_validation_failed"
    # LLMError wraps JSONDecodeError and the empty-response sentinel.
    if isinstance(cause, json.JSONDecodeError) or str(error).startswith("invalid JSON"):
        return "invalid_json"
    if isinstance(cause, (TimeoutError, openai.APITimeoutError)):
        return "timeout"
    if isinstance(cause, openai.APIConnectionError):
        return "connection_error"
    if isinstance(cause, openai.APIStatusError):
        return "server_error" if cause.status_code >= 500 else f"http_{cause.status_code}"
    if str(cause) == "empty response":
        return "empty_response"
    if str(cause) == "slm empty primary":
        return "slm_empty_primary"
    return "call_failed"


def track_model_selection(operation: str | None = None):
    """Finalize at the outermost same-task operation, after caller validation.

    Wrapping _call, _call_json and the workflow preserves their return types while
    keeping responses provisional until the outermost workflow accepts them.
    """

    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            name = operation or kwargs.get("name", "persona-llm-call")
            parent = current_model_selection()
            if parent is not None and parent.operation == name:
                return await function(*args, **kwargs)
            metadata = kwargs.get("trace_metadata") or kwargs.get("metadata") or {}
            operation_id = metadata.get("model_operation_id") or uuid4().hex
            selection = ModelSelection(name, operation_id, asyncio.current_task())
            token = _selection.set(selection)
            try:
                result = await function(*args, **kwargs)
                if selection.final_source is None:
                    selection.final_source = selection.response_source or "unknown"
                    selection.final_model = selection.response_model or selection.requested_model
                return result
            except BaseException as error:
                selection.final_source = "failed"
                selection.final_model = None
                selection.failure_reason = model_failure_reason(error)
                raise
            finally:
                try:
                    _model_event("model-result")
                finally:
                    _selection.reset(token)

        return wrapped

    return decorate


def record_model_selection(*, source: str, skip_reason: str | None) -> None:
    if selection := current_model_selection():
        # A JSON retry explicitly disables SLM; preserve the original attempted decision.
        if not selection.slm_attempted:
            selection.slm_skip_reason = skip_reason
        _model_event("model-selection", selected_source=source)


def begin_model_attempt(model: str, *, source: str, attempt: int) -> dict:
    if selection := current_model_selection():
        selection.slm_attempted |= source == "slm"
        selection.requested_model = model
        selection.response_model = None
        selection.response_source = None
        selection.final_model = None
        selection.final_source = None
        selection.failure_reason = None
        selection.rule_reason = None
        _model_event("model-call-attempt", model_source=source, attempt=attempt)
        return selection.metadata()
    return {}


def record_model_response(response, *, source: str) -> None:
    if selection := current_model_selection():
        model = getattr(response, "model", None)
        selection.response_model = model if isinstance(model, str) and model else None
        selection.response_source = source
        _model_event("model-response", model_source=source)


def record_model_fallback(error: BaseException) -> None:
    if selection := current_model_selection():
        selection.fallback_reason = model_failure_reason(error)
        _model_event("model-fallback")


def record_model_failure(reason: str) -> None:
    if selection := current_model_selection():
        selection.final_source = "failed"
        selection.final_model = None
        selection.failure_reason = reason


def record_rule_result(reason: str, *, mixed: bool = False) -> None:
    if selection := current_model_selection():
        mixed = mixed and selection.final_source != "rule_based"
        selection.final_source = "mixed" if mixed else "rule_based"
        selection.final_model = (selection.response_model or selection.requested_model) if mixed else None
        selection.rule_reason = reason
        if selection.fallback_reason is None:
            selection.fallback_reason = reason


def record_consumer_rule_result(*, operation: str, operation_id: str, reason: str) -> None:
    """The consumer can adopt rules while a background model is still running.

    Do not infer that task's attempted/model fields here; its independent result
    is correlated by trace and operation ID, even if it finishes after this response.
    """
    _write_model_event(
        "model-consumer-result",
        {
            "model_operation": operation,
            "model_operation_id": operation_id,
            "final_source": "rule_based",
            "final_model": None,
            "rule_reason": reason,
        },
    )


def build_langfuse_metadata(
    *,
    feature: str,
    operation: str,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: tuple[str, ...] = (),
    **attributes: MetadataScalar | None,
) -> LangfuseMetadata:
    """OpenAI 래퍼에 전달할 표준 메타데이터를 만든다.

    ``user_id``와 ``session_id``는 Langfuse가 인식하는 예약 키로 변환한다.
    추가 속성은 필터에 사용하기 쉬운 값만 허용하며 ``None``은 기록하지 않는다.
    예약 키는 호출자가 덮어쓸 수 없다.
    """

    metadata: LangfuseMetadata = {
        "feature": feature,
        "operation": operation,
        "langfuse_tags": list(dict.fromkeys((feature, operation, *tags))),
    }
    if user_id:
        metadata["langfuse_user_id"] = user_id
    if session_id:
        metadata["langfuse_session_id"] = session_id

    for key, value in attributes.items():
        if value is None or key in _LANGFUSE_RESERVED_KEYS:
            continue
        metadata[key] = value
    return metadata


def _propagated_value(value: MetadataScalar) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


@contextmanager
def propagate_langfuse_metadata(metadata: LangfuseMetadata | None) -> Iterator[None]:
    """현재 관측과 하위 관측에 사용자·세션·필터 속성을 전파한다.

    Langfuse v4의 전파 메타데이터 제한에 맞춰 일반 값은 문자열로 변환한다.
    실제 프롬프트나 응답, 닉네임 같은 개인정보는 이 함수에 전달하지 않는다.
    """

    if not metadata:
        yield
        return

    raw_tags = metadata.get("langfuse_tags", [])
    tags = [str(tag) for tag in raw_tags] if isinstance(raw_tags, list) else []
    propagated = {
        key: _propagated_value(value)
        for key, value in metadata.items()
        if key not in _LANGFUSE_RESERVED_KEYS and not isinstance(value, list)
    }
    stack = ExitStack()
    try:
        stack.enter_context(
            propagate_attributes(
                user_id=str(metadata["langfuse_user_id"]) if metadata.get("langfuse_user_id") else None,
                session_id=str(metadata["langfuse_session_id"]) if metadata.get("langfuse_session_id") else None,
                tags=tags or None,
                metadata=propagated or None,
            )
        )
    except Exception:
        logger.debug("failed to propagate Langfuse metadata", exc_info=True)
    try:
        yield
    finally:
        try:
            stack.close()
        except Exception:
            logger.debug("failed to close Langfuse metadata context", exc_info=True)
