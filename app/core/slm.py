"""태깅·추출 전용 소형 모델(SLM) 라우팅.

대화·질문 생성은 메인 LLM(OpenRouter Gemini)이 그대로 맡는다. 출력이 짧고 정답이 정해진
태깅·추출만 RunPod vLLM 에 올린 Kanana(+LoRA)로 보낸다. 서빙 쪽 설정은 serving/vllm/ 에 있다.
SLM_BASE_URL 이 비어 있으면 이 모듈은 아무것도 하지 않는다.

SLM 은 메인 LLM 을 대신하는 게 아니라 앞에 서는 것이다. 실패하면 호출부(persona.agents._call)가
같은 요청을 메인 LLM 으로 다시 보낸다.

콜드 스타트: Pod 를 켜면 모델을 올리는 몇 분 동안 vLLM 이 응답하지 않는다(프록시 502·연결 실패).
그동안 매 요청이 SLM 실패를 겪고 폴백하면 요청마다 지연이 붙는다. 그래서 한 번 실패하면 SLM 을
'내려감'으로 표시하고 이후 요청은 곧장 메인 LLM 으로 보낸다. 복구 확인은 사용자 요청이 아니라
백그라운드에서 /health 를 주기적으로 확인해서 한다. 앱 시작 때도 한 번 확인해 첫 요청 전에 상태를 안다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache

import httpx
import openai
from langfuse import get_client as get_langfuse
from langfuse.openai import AsyncOpenAI

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# LLM 호출 이름(name=) → 작업. 여기 없는 호출은 항상 메인 LLM 으로 간다
TASKS = {
    "persona-tagging": "tagging",
    "persona-extraction": "extraction",
    "persona-extraction-style": "extraction",
}

# 서버가 없거나 아직 안 떴다는 신호. 이런 실패면 SLM 을 내려감으로 표시한다.
# 404 는 RunPod 프록시가 꺼진 Pod 에 주는 응답이기도 하고, 모델 이름 오타이기도 하다 — 둘 다 폴백이 맞다
_DOWN = (
    TimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
    openai.NotFoundError,
)

_HEALTH_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class Route:
    client: AsyncOpenAI
    model: str


@lru_cache
def _get_client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(
        base_url=settings.slm_base_url,
        api_key=settings.slm_api_key or "EMPTY",
        # SDK 자체 재시도는 끈다. 실패하면 재시도 대신 메인 LLM 으로 넘기는 게 이 모듈의 규칙이다.
        # 연결은 짧게 — 꺼진 Pod 에 붙으려고 호출 예산을 다 쓰지 않게
        max_retries=0,
        timeout=httpx.Timeout(60.0, connect=3.0),
    )


def health_url(base_url: str) -> str:
    """https://x/v1 → https://x/health. vLLM 의 /health 는 /v1 밖에 있고 API 키 없이 열린다."""
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return f"{base}/health"


async def check_health() -> bool:
    """vLLM 이 모델을 다 올려 요청을 받을 수 있으면 True."""
    url = health_url(get_settings().slm_base_url)
    try:
        async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT_S) as client:
            resp = await client.get(url)
    except httpx.HTTPError:
        return False
    return resp.status_code == 200


class Health:
    """SLM 이 살아 있는지. 내려가면 백그라운드에서 /health 가 200 이 될 때까지 확인한다."""

    def __init__(self, check: Callable[[], Awaitable[bool]] = check_health) -> None:
        self.up = True
        self._check = check
        self._probe: asyncio.Task | None = None
        self._startup: asyncio.Task | None = None

    def mark_down(self, reason: str) -> None:
        if self.up:
            logger.warning("SLM marked down, routing to main LLM: %s", reason)
        self.up = False
        if self._probe is None or self._probe.done():
            self._probe = asyncio.create_task(self._probe_until_up())

    async def _probe_until_up(self) -> None:
        interval = get_settings().slm_probe_interval_s
        while not self.up:
            await asyncio.sleep(interval)
            if await self._check():
                self.up = True
                logger.info("SLM is healthy again, routing tagging/extraction back to it")

    def check_in_background(self) -> None:
        """앱 시작 때 한 번. 아직 모델을 올리는 중이면 내려감으로 두고 복구를 기다린다.
        시작을 막지 않도록 백그라운드로 돈다 (태스크는 참조를 잡아 둬야 GC 되지 않는다)."""
        self._startup = asyncio.create_task(self.check_now())

    async def check_now(self) -> None:
        if not await self._check():
            self.mark_down("startup health check failed")

    async def stop(self) -> None:
        for task in (self._startup, self._probe):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


health = Health()


def enabled() -> bool:
    return bool(get_settings().slm_base_url)


def route_for(name: str) -> Route | None:
    """이 호출을 SLM 으로 보낼 수 있으면 그 경로. 꺼져 있거나 대상이 아니거나 내려가 있으면 None."""
    if not enabled() or not health.up:
        return None
    settings = get_settings()
    model = {
        "tagging": settings.slm_tagging_model,
        "extraction": settings.slm_extraction_model,
    }.get(TASKS.get(name, ""), "")
    if not model:
        return None
    return Route(client=_get_client(), model=model)


def record_fallback(name: str, error: BaseException) -> None:
    """SLM 실패를 기록한다. 서버가 없다는 신호면 내려감으로 표시해 다음 요청부터 바로 메인 LLM 으로 보낸다."""
    cause = error.__cause__ or error
    if isinstance(cause, _DOWN):
        health.mark_down(f"{name}: {type(cause).__name__}")
    logger.warning("SLM call %s failed, falling back to main LLM: %s", name, error)
    try:
        get_langfuse().create_event(
            name="slm-fallback",
            level="WARNING",
            status_message=str(error),
            metadata={"call": name, "error_type": type(cause).__name__},
        )
    except Exception:  # noqa: BLE001 - 관측 실패가 폴백 흐름을 깨면 안 된다
        logger.debug("failed to record slm fallback event", exc_info=True)


async def start() -> None:
    """앱 시작 시 호출."""
    if enabled():
        health.check_in_background()


async def stop() -> None:
    await health.stop()
