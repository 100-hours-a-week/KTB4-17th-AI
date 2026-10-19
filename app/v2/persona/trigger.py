"""v2 메모리 업데이트 트리거 판정 (미처리 사용자 8턴 / 20분 무응답 / 중요 신호)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.features.practice.models import PracticeMessage

from .models import UserPersonaCore

_IMPORTANT_SIGNAL_PATTERNS = [
    re.compile(r"기억해\s*줘|기억해\s*두|잊지\s*마"),
    re.compile(r"(이사|이직|퇴사|취업|입사|전근|복학|졸업)\s*(했|함|해요|했어|할\s*거|예정)"),
    re.compile(r"(서울|부산|대구|인천|광주|대전|울산|세종|수원|성남|판교|강남|홍대|마포|제주)[으]?로\s*이사"),
    re.compile(r"직업[은이]|회사\s*옮겼|일\s*바꿨"),
]


def has_important_signal(user_message: str) -> bool:
    """'기억해줘', 이사·이직 등 코어 프로필 변경/중요 사실 신호를 감지한다."""
    return any(p.search(user_message) for p in _IMPORTANT_SIGNAL_PATTERNS)


async def unprocessed_user_message_count(db: AsyncSession, user_id: str) -> int:
    """커서(last_processed_message_id) 이후에 쌓인 이 사용자의 발화 수."""
    core = await db.get(UserPersonaCore, user_id)
    last_id = core.last_processed_message_id if core else 0
    stmt = select(func.count(PracticeMessage.id)).where(
        PracticeMessage.user_id == user_id,
        PracticeMessage.role == "user",
        PracticeMessage.id > last_id,
    )
    return int((await db.execute(stmt)).scalar_one() or 0)


async def should_trigger_memory_update(
    db: AsyncSession,
    *,
    user_id: str,
    latest_user_message: str,
) -> tuple[bool, str | None]:
    """(트리거 여부, 사유)를 반환한다. 사유: 'signal' | 'turn_count' | 'idle' | None.

    'idle' 은 마지막 활동 후 시간이 지난 뒤에 호출될 때만 참이 된다. 응답 직후 호출(maybe_run_memory_update)은
    활동 시각을 방금 갱신했으므로 거의 거짓이고, 실제 20분 무응답 트리거는 스위퍼가 맡는다.
    """
    settings = get_settings()

    if has_important_signal(latest_user_message):
        return True, "signal"

    unprocessed_turns = await unprocessed_user_message_count(db, user_id)
    if unprocessed_turns >= settings.memory_trigger_turn_count:
        return True, "turn_count"

    core = await db.get(UserPersonaCore, user_id)
    if core and core.last_activity_at and unprocessed_turns > 0:
        last_act = core.last_activity_at
        if last_act.tzinfo is None:  # SQLite
            last_act = last_act.replace(tzinfo=UTC)
        if datetime.now(UTC) - last_act >= timedelta(minutes=settings.memory_trigger_idle_minutes):
            return True, "idle"

    return False, None
