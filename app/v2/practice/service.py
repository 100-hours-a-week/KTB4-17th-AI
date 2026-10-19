"""v2 연습대화 서비스 — v1 PracticeService 를 상속하고 메모리 관련 훅만 바꾼다."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.guardrail import GuardrailContext
from app.features.practice.agents import OPENING_INSTRUCTION
from app.features.practice.models import PracticeSession
from app.features.practice.service import PracticeService, SessionEnded
from app.v2.persona.embedder import EmbedderProtocol, get_embedder
from app.v2.persona.repository import PersonaMemoryRepository

from .memory import MemoryContext, grounding_texts, load_memory_context, render_memory_section
from .models import V2PracticeSpeechState
from .speech import (
    ASK_AFTER_TURNS,
    MODE_ASKED,
    MODE_CASUAL,
    MODE_DECLINED,
    MODE_FORMAL,
    asks_permission,
    classify_permission,
    speech_addon,
)

logger = logging.getLogger(__name__)


class V2PracticeService(PracticeService):
    def __init__(self, db: AsyncSession, embedder: EmbedderProtocol | None = None) -> None:
        super().__init__(db)
        self.embedder = embedder or get_embedder()  # 모델은 첫 호출 때 로드된다
        self._memory = MemoryContext()  # _system 이 채우고 _guardrail_context 가 읽는다 (요청마다 새 인스턴스)
        self._ask_pending = False  # 이번 답변에서 말 놓기를 제안하도록 지시했는지 — 저장 때 asked 로 넘길지 정한다

    async def _load_memory(self, session: PracticeSession) -> MemoryContext:
        if not session.user_id:  # 내 페르소나 없이 시작한 세션 — 메모리 없이 v1 처럼 동작
            return MemoryContext()
        last = session.messages[-1] if session.messages else None
        query = last.content if last is not None and last.role == "user" else None  # 오프닝은 검색 안 함
        try:
            return await load_memory_context(self.db, user_id=session.user_id, query=query, embedder=self.embedder)
        except Exception:  # noqa: BLE001 - 메모리 장애(모델 파일 없음 등)가 대화를 막으면 안 된다
            logger.exception("v2 memory retrieval failed for user=%s — continuing without memory", session.user_id)
            return MemoryContext()

    async def _system(self, session: PracticeSession) -> str:
        self._memory = await self._load_memory(session)  # super()._system 이 _guardrail_context 를 부르기 전에
        system = await super()._system(session)
        if not self._memory.is_empty:
            system += "\n" + render_memory_section(session.my_nickname, self._memory)
        return system + "\n" + await self._speech_section(session)

    # ── 말 놓기 제안 ──────────────────────────────────────

    async def _speech_state(self, session: PracticeSession) -> V2PracticeSpeechState:
        state = await self.db.get(V2PracticeSpeechState, session.id)
        if state is None:
            state = V2PracticeSpeechState(session_id=session.id, mode=MODE_FORMAL)
            self.db.add(state)
            await self.db.flush()
        return state

    async def _user_declined(self, session: PracticeSession) -> bool:
        if not session.user_id:
            return False
        core = await PersonaMemoryRepository(self.db).get_core(session.user_id)
        return core is not None and core.casual_speech_declined_at is not None

    # 이번 답변에서 말투 고정/허락/제안 중 무엇을 지시할지 정해 프롬프트 조각으로 만든다
    async def _speech_section(self, session: PracticeSession) -> str:
        state = await self._speech_state(session)
        mode = state.mode
        if mode != MODE_CASUAL and await self._user_declined(session):
            mode = MODE_DECLINED  # 다른 세션에서 거절한 사용자 — 이 세션에서도 묻지 않는다
        user_turns = sum(1 for m in session.messages if m.role == "user")
        self._ask_pending = (
            mode == MODE_FORMAL
            and user_turns >= ASK_AFTER_TURNS
            and bool(session.messages)
            and session.messages[-1].role == "user"
        )
        return speech_addon(me=session.my_nickname, mode=mode, ask_now=self._ask_pending)

    # 제안한 뒤 처음 오는 사용자 답을 판정해 casual/declined 로 확정한다. 거절은 사용자 단위로도 남긴다
    async def _resolve_pending_question(self, session: PracticeSession, message: str) -> None:
        state = await self._speech_state(session)
        if state.mode != MODE_ASKED:
            return
        allow = await classify_permission(message)
        now = datetime.now(UTC)
        state.mode = MODE_CASUAL if allow else MODE_DECLINED
        state.answered_at = now
        if not allow and session.user_id:
            core = await PersonaMemoryRepository(self.db).get_or_create_core(session.user_id)
            core.casual_speech_declined_at = now
        await self.db.flush()  # 내 메시지 저장 커밋과 함께 반영된다

    async def stream_reply(self, session: PracticeSession, message: str) -> AsyncIterator:
        if session.status != "active":
            raise SessionEnded(session.id)
        await self._resolve_pending_question(session, message)
        async for ev in super().stream_reply(session, message):
            yield ev

    # 말 놓기를 제안하라고 지시한 답변이 실제로 제안을 담았을 때만 asked 로 넘긴다. 아니면 다음 턴에 다시 시도한다
    async def _save_message(self, session: PracticeSession, role: str, content: str, source: str | None = None) -> None:
        if role == "persona" and self._ask_pending and source == "llm" and asks_permission(content):
            state = await self._speech_state(session)
            state.mode = MODE_ASKED
            state.asked_at = datetime.now(UTC)
            self._ask_pending = False
        await super()._save_message(session, role, content, source)

    def _guardrail_context(self, session, partner, me) -> GuardrailContext:
        ctx = super()._guardrail_context(session, partner, me)  # v1 은 staticmethod — super() 로 그대로 호출된다
        return ctx.model_copy(update={"user_texts": [*ctx.user_texts, *grounding_texts(self._memory)]})

    @staticmethod
    def _history(session: PracticeSession) -> list[dict]:
        window = get_settings().practice_v2_history_window
        msgs = [
            {"role": "user" if m.role == "user" else "assistant", "content": m.content}
            for m in session.messages[-window:]
        ]
        if msgs and msgs[0]["role"] == "assistant":
            msgs.insert(0, {"role": "user", "content": OPENING_INSTRUCTION})
        return msgs
