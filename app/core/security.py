"""내부 API 인증 의존성."""

from fastapi import Header, HTTPException

from app.core.config import get_settings


def require_internal_key(
    x_internal_api_key: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> None:
    """`X-Internal-Api-Key` 또는 `X-API-Key` 중 하나가 내부 키와 같으면 통과시킨다.

    백엔드 AI 연습 클라이언트는 `X-API-Key` 이름으로 키를 보내므로 두 이름을 모두 받는다.
    """
    expected = get_settings().internal_api_key
    if expected and expected not in (x_internal_api_key, x_api_key):
        raise HTTPException(401, "invalid internal api key")
