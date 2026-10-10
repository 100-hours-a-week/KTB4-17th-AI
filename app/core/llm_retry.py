"""LLM 호출 재시도 규칙.

운영 Langfuse 기록을 보면 실패의 대부분은 우리 쪽 타임아웃(asyncio.timeout)에 걸린 경우였고,
같은 요청을 몇 초 뒤 다시 보내면 대개 성공했다. OpenAI SDK 도 429·5xx·연결 오류는 스스로
재시도하지만 그건 우리 타임아웃 안에서 일어나 예산을 같이 쓴다. 그래서 기능 코드는 시도마다
타임아웃을 새로 잡고, 아래 규칙에 맞는 실패만 한 번 더 보낸다.

재시도하지 않는 실패: 인증·요청 형식 오류(4xx)처럼 다시 보내도 같은 결과인 것.
"""

from __future__ import annotations

import openai
from langfuse import get_client

from app.core.config import get_settings

# 재시도 전 잠깐 쉰다 — 순간 지연·요청 한도에 바로 같은 요청을 겹쳐 보내지 않게
RETRY_BACKOFF_S = 0.5

_RETRYABLE = (
    TimeoutError,  # 우리 asyncio.timeout
    openai.APIConnectionError,  # 연결 실패·SDK 자체 타임아웃(APITimeoutError 포함)
    openai.RateLimitError,
    openai.InternalServerError,
)


def attempts() -> int:
    """첫 시도를 포함한 총 시도 횟수. LLM_RETRY_ATTEMPTS=0 이면 재시도하지 않는다."""
    return 1 + get_settings().llm_retry_attempts


def is_retryable(error: BaseException) -> bool:
    return isinstance(error, _RETRYABLE)


def record_retry(*, name: str, attempt: int, reason: str) -> None:
    """재시도를 Langfuse 에 이벤트로 남긴다.

    타임아웃으로 끊긴 시도는 generation 기록이 남지 않아서, 이게 없으면 재시도 끝에 성공한
    호출은 대시보드에서 정상과 구분되지 않는다. 현재 span(기능 워크플로)의 자식으로 붙는다."""
    get_client().create_event(
        name="llm-retry",
        level="WARNING",
        status_message=reason,
        metadata={"call": name, "attempt": attempt},
    )
