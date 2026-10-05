"""기능 API에서 공통으로 사용하는 오류 매핑."""

from fastapi import HTTPException

from app.core.media import InvalidImage


class ModelUnavailable(RuntimeError):
    pass


def raise_api_error(exc: Exception) -> None:
    if isinstance(exc, InvalidImage):
        raise HTTPException(422, str(exc)) from exc
    if isinstance(exc, ModelUnavailable):
        raise HTTPException(503, str(exc)) from exc
    raise exc
