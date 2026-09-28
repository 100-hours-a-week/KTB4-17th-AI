"""Langfuse 관측 메타데이터의 공통 규칙.

기능 코드가 Langfuse 예약 필드를 직접 조립하지 않도록 한곳에서 관리한다.
프롬프트와 응답은 OpenAI 래퍼가 기록하고, 이 모듈은 호출을 사용자·세션·기능별로
찾을 수 있게 만드는 식별 정보만 다룬다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from langfuse import propagate_attributes

type MetadataScalar = str | int | float | bool
type LangfuseMetadata = dict[str, MetadataScalar | list[str]]

_LANGFUSE_RESERVED_KEYS = {
    "langfuse_user_id",
    "langfuse_session_id",
    "langfuse_tags",
}


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
    with propagate_attributes(
        user_id=str(metadata["langfuse_user_id"]) if metadata.get("langfuse_user_id") else None,
        session_id=str(metadata["langfuse_session_id"]) if metadata.get("langfuse_session_id") else None,
        tags=tags or None,
        metadata=propagated or None,
    ):
        yield
