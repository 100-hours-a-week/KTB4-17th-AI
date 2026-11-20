"""말투 어댑터 서빙 — 어떤 어댑터로 답할지 고르고, 상시 서빙 vLLM(OpenAI 호환)에 요청한다 (MLOps 설계서 7-4절).

고르기 (persona-mlops src/serving/adapter_selector.py 와 같은 규칙)
  - persona_adapter 에서 그 사용자의 ACTIVE 행을 찾는다.
  - 그 행의 base_model_id 가 지금 서빙 중인 베이스(MLOPS_SERVING_BASE_MODEL_ID)와 같을 때만 쓴다.
    베이스를 바꾼 직후 아직 재학습되지 않은 사용자는 기존 경로로 답한다.
  - 서빙 이름은 "{user_id}-v{version}" 이다. vLLM 요청의 model 에 그대로 넣는다.

켜는 조건: MLOPS_ADAPTER_ENABLED 가 참이고 MLOPS_SERVING_BASE_URL 이 있을 때. DB 의 어댑터를 쓰려면
MLOPS_SERVING_BASE_MODEL_ID 도 있어야 한다 (비교할 베이스가 없으면 고르지 않는다).
MLOPS_ADAPTER_USER_ALLOWLIST 가 비어 있으면 모든 사용자, 있으면 그 user_id(쉼표 구분)만.

[임시 시험용] MLOPS_ADAPTER_TEST_NICKNAME_MAP ("닉네임=어댑터이름,…"): 운영 DB 에 어댑터 행을 넣을 수 없고 시험할
사용자의 user_id 도 몰라서 둔다. 상대 페르소나 닉네임이 키와 똑같으면 그 어댑터 이름을 쓴다. DB 조회보다 먼저 보고,
베이스 확인과 허용 목록은 건너뛴다. 같은 닉네임의 페르소나는 모두 걸린다. 운영 시험이 끝나면 이 설정과 코드를 지운다.

이 모듈은 실패를 삼키지 않는다 (complete 는 AdapterError). 기존 경로로 넘기는 것은 호출부의 몫이다.
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache

import httpx
from langfuse import get_client as get_langfuse
from langfuse.openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.observability import LangfuseMetadata

from .models import ACTIVE, PersonaAdapter
from .prompt import Message

logger = logging.getLogger(__name__)

# 학습 뒤 평가 응답을 만들 때 값 (persona-mlops training.config.EvalConfig). 서빙도 같은 값으로 뽑는다
TEMPERATURE = 0.7
TOP_P = 0.95
MAX_TOKENS = 160


class AdapterError(Exception):
    """서빙 호출 실패 (연결 실패, 타임아웃, 모르는 어댑터 이름, 빈 답 등). 호출부가 기존 경로로 답한다."""


def adapter_name(user_id: str, version: int) -> str:
    return f"{user_id}-v{version}"


def enabled() -> bool:
    """어댑터 서빙을 부를 수 있는가 (켜져 있고 주소가 있다). 턴마다 다시 봐서, 끄면 다음 턴부터 기존 경로로 간다."""
    s = get_settings()
    return bool(s.mlops_adapter_enabled and s.mlops_serving_base_url)


def allowlist() -> frozenset[str]:
    """MLOPS_ADAPTER_USER_ALLOWLIST 의 user_id 들. 비어 있으면 모든 사용자를 뜻한다."""
    raw = get_settings().mlops_adapter_user_allowlist
    return frozenset(u.strip() for u in raw.split(",") if u.strip())


def allowed(user_id: str) -> bool:
    users = allowlist()
    return not users or user_id in users


# 어댑터 이름은 practice_sessions.adapter_model(varchar 96)에 들어간다
_MAX_ADAPTER_NAME = 96


def trial_nickname_map() -> dict[str, str]:
    """[임시 시험용] MLOPS_ADAPTER_TEST_NICKNAME_MAP → {닉네임: 어댑터 이름}.

    항목은 쉼표로, 닉네임과 이름은 "=" 로 나눈다. 앞뒤 공백은 걷는다. "=" 가 없거나 한쪽이 비었거나, 이름에
    공백·"=" 가 있거나 96자를 넘는 항목은 버린다. 같은 닉네임이 두 번 나오면 뒤의 것을 쓴다.
    """
    out: dict[str, str] = {}
    for entry in get_settings().mlops_adapter_test_nickname_map.split(","):
        nickname, sep, name = entry.partition("=")
        nickname, name = nickname.strip(), name.strip()
        if not sep or not nickname or not name:
            continue
        if "=" in name or any(c.isspace() for c in name) or len(name) > _MAX_ADAPTER_NAME:
            logger.warning("ignoring malformed MLOPS_ADAPTER_TEST_NICKNAME_MAP entry for adapter %r", name[:40])
            continue
        out[nickname] = name
    return out


async def get_active(db: AsyncSession, user_id: str) -> PersonaAdapter | None:
    stmt = select(PersonaAdapter).where(PersonaAdapter.user_id == user_id, PersonaAdapter.status == ACTIVE)
    return (await db.execute(stmt)).scalar_one_or_none()


async def select_adapter(db: AsyncSession, user_id: str | None, nickname: str | None = None) -> str | None:
    """페르소나 주인의 어댑터 서빙 이름. 꺼져 있거나, 없거나, 베이스가 다르면 None.

    nickname(상대 페르소나 닉네임)이 시험용 닉네임 표에 있으면 그 이름이 먼저다 (DB·베이스·허용 목록을 보지 않는다).
    조회가 실패해도 None 이다. SAVEPOINT 안에서 조회해, PostgreSQL 에서 실패한 문장이 바깥 트랜잭션
    (이어서 만드는 세션 행)을 깨지 않게 한다.
    """
    if not enabled():
        return None
    if nickname and (test_name := trial_nickname_map().get(nickname)):
        logger.info("mlops adapter %s chosen by the test nickname map", test_name)
        return test_name
    if not user_id or not allowed(user_id) or not get_settings().mlops_serving_base_model_id:
        return None
    try:
        async with db.begin_nested():
            row = await get_active(db, user_id)
    except Exception:  # noqa: BLE001 - 어댑터 조회 실패가 대화 시작을 막으면 안 된다
        logger.exception("mlops adapter lookup failed for user=%s, using existing reply path", user_id)
        return None
    if row is None:
        return None
    base = get_settings().mlops_serving_base_model_id
    if row.base_model_id != base:
        logger.info(
            "mlops adapter %s skipped: base %s != serving base %s",
            adapter_name(row.user_id, row.version),
            row.base_model_id,
            base,
        )
        return None
    return adapter_name(row.user_id, row.version)


@lru_cache
def get_client() -> AsyncOpenAI:
    s = get_settings()
    return AsyncOpenAI(
        base_url=s.mlops_serving_base_url,
        api_key=s.mlops_serving_api_key or "EMPTY",
        # 재시도 대신 기존 경로로 넘긴다. 꺼진 Pod 에 붙으려고 오래 기다리지 않게 연결은 짧게
        max_retries=0,
        timeout=httpx.Timeout(s.mlops_serving_timeout_s, connect=s.mlops_serving_connect_timeout_s),
    )


async def complete(messages: list[Message], *, model: str, name: str, metadata: LangfuseMetadata | None = None) -> str:
    """어댑터 응답 원문 하나 (스트리밍하지 않는다 — 앞에 붙는 이름 라벨을 떼고 내보내야 해서).

    실패는 모두 AdapterError. 전체 시간은 MLOPS_SERVING_TIMEOUT_S 를 넘기지 않는다.
    """
    try:
        async with asyncio.timeout(get_settings().mlops_serving_timeout_s):
            resp = await get_client().chat.completions.create(
                name=name,
                model=model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                top_p=TOP_P,
                metadata=metadata,
            )
    except Exception as e:
        raise AdapterError(f"{type(e).__name__}: {e}") from e
    if not resp.choices:
        raise AdapterError("empty choices")
    return resp.choices[0].message.content or ""


def record_fallback(model: str | None, error: BaseException) -> None:
    """어댑터 실패 기록. 서빙이 꺼졌어도 사용자 요청은 기존 경로로 이어진다."""
    cause = error.__cause__ or error
    logger.warning("mlops adapter %s failed, falling back to existing reply path: %s", model, error)
    try:
        get_langfuse().create_event(
            name="adapter-fallback",
            level="WARNING",
            status_message=str(error),
            metadata={"call": "practice-reply-adapter", "adapter": model, "error_type": type(cause).__name__},
        )
    except Exception:  # noqa: BLE001 - 관측 실패가 폴백 흐름을 깨면 안 된다
        logger.debug("failed to record adapter fallback event", exc_info=True)
