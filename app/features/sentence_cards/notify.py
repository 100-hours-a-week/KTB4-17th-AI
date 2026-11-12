"""학습 후보 알림 (Discord 웹훅). 묶음 응답을 보낸 뒤 백그라운드에서 돈다.

아직 알리지 않은 학습 후보를 모두 보낸다. 웹훅 실패나 미설정이면 notified_at 을 비워 두고
다음 묶음 때 다시 보낸다. 알림에는 개수만 담고 문장·사용자 정보는 담지 않는다.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings

from .models import SentenceTrainingTrigger
from .repository import SentenceCardRepository
from .verdict import TRIGGER_AGREE, TRIGGER_DISAGREE_REVIEWED

logger = logging.getLogger(__name__)

Sender = Callable[[str], Awaitable[None]]

_TIMEOUT_S = 5.0


def discord_sender() -> Sender | None:
    url = get_settings().training_discord_webhook_url
    if not url:
        return None

    async def send(content: str) -> None:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            (await client.post(url, json={"content": content})).raise_for_status()

    return send


def message(trigger: SentenceTrainingTrigger) -> str:
    return (
        f"[문장 판정 카드] 학습 후보 #{trigger.seq}\n"
        f"AGREE {trigger.agree}개 · 사람 확인된 DISAGREE {trigger.disagree_reviewed}개 "
        f"(직전 후보보다 각각 {TRIGGER_AGREE}개 · {TRIGGER_DISAGREE_REVIEWED}개 이상 늘었습니다)\n"
        "학습용 RunPod Pod 를 켜고 학습 파이프라인을 실행해 주세요."
    )


async def notify_pending(
    factory: async_sessionmaker[AsyncSession],
    send: Sender | None,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """보낸 알림 수."""
    async with factory() as db:
        triggers = await SentenceCardRepository(db).unnotified_triggers()
        if not triggers:
            return 0
        if send is None:
            logger.warning(
                "sentence training trigger not notified: TRAINING_DISCORD_WEBHOOK_URL is empty (pending=%s)",
                [t.seq for t in triggers],
            )
            return 0
        sent = 0
        for trigger in triggers:
            try:
                await send(message(trigger))
            except httpx.HTTPError as e:
                logger.warning("sentence training trigger notify failed: seq=%d error=%s", trigger.seq, e)
                break
            trigger.notified_at = now()
            sent += 1
        await db.commit()
    if sent:
        logger.info("sentence training trigger notified: count=%d", sent)
    return sent
