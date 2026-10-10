"""내부 API 인증 의존성."""

from fastapi import Header, HTTPException

from app.core.config import get_settings


def require_internal_key(x_internal_api_key: str | None = Header(default=None)) -> None:
    expected = get_settings().internal_api_key
    if expected and x_internal_api_key != expected:
        raise HTTPException(401, "invalid internal api key")
