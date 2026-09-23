"""연습대화 — 결정하는 곳.

  start(req)               상대(저장된 페르소나) 고르고 세션을 연다. LLM 없음
  stream_opening(session)  상대가 먼저 인사 (SSE)
  stream_reply(session, m) 내 메시지 저장 → 상대 답변 스트리밍 → 답변 저장 (SSE)

스트리밍 함수는 (이벤트 이름, data 모델) 튜플을 낸다. SSE 직렬화는 api.py 가 한다.
커밋도 여기서 한다 — 라우트는 StreamingResponse 를 돌려준 뒤 끝나므로, 커밋할 자리가 제너레이터 안뿐이다.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.persona.lookup import LoadedPersona, load_persona
from app.features.persona.schemas import PersonaRef

from .agents import FALLBACK_REPLY, OPENING_INSTRUCTION, LLMError, PartnerAgent
from .models import PracticeSession
from .repository import PracticeRepository
from .schemas import (
    HISTORY_WINDOW,
    DeltaEvent,
    DoneEvent,
    ErrorEvent,
    PracticeMessageItem,
    PracticeSessionResponse,
    PracticeStartRequest,
    PracticeStartResponse,
    StartEvent,
)

logger = logging.getLogger(__name__)

Event = tuple[str, BaseModel]


class PersonaNotFound(Exception):
    # who(partner|me) 와 어떤 참조로 찾았는지를 들고 다니는 예외 — 호출부가 404 메시지를 만들 때 씀
    def __init__(self, who: str, ref: PersonaRef) -> None:
        self.who = who
        self.ref = ref
        super().__init__(f"{who}: persona not found for {ref.describe()}")


class SessionEnded(Exception):
    pass


class PracticeService:
    # 이 요청의 DB 세션에 묶인 repository/agent 를 만든다
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = PracticeRepository(db)
        self.agent = PartnerAgent()

    # PersonaRef 로 저장된 페르소나를 불러온다. 없으면 PersonaNotFound
    async def _load(self, who: str, ref: PersonaRef) -> LoadedPersona:
        loaded = await load_persona(self.db, ref)
        if loaded is None:
            raise PersonaNotFound(who, ref)
        return loaded

    # ── 세션 ──────────────────────────────────────────────

    # 상대(+선택적으로 나) 페르소나를 로드해 세션 row 를 만든다. LLM 호출 없음
    async def start(self, req: PracticeStartRequest) -> PracticeStartResponse:
        partner = await self._load("partner", req.partner)
        me = await self._load("me", req.me) if req.me else None
        my_nickname = req.nickname or (me.nickname if me else "회원")

        session = await self.repo.create_session(
            partner_persona_id=partner.record.id,
            partner_user_id=partner.record.user_id,
            partner_nickname=partner.nickname,
            my_persona_id=me.record.id if me else None,
            user_id=me.record.user_id if me else None,
            my_nickname=my_nickname,
        )
        return PracticeStartResponse(
            session_id=session.id,
            partner=partner.brief,
            my_nickname=my_nickname,
            message_count=0,
            created_at=session.created_at,
        )

    # 세션 + 메시지 목록을 응답 스키마로 조립. 상대 페르소나가 지워졌으면 최소 정보로 대체
    async def get(self, session: PracticeSession) -> PracticeSessionResponse:
        partner = await load_persona(self.db, PersonaRef(persona_id=session.partner_persona_id))
        return PracticeSessionResponse(
            session_id=session.id,
            partner=partner.brief if partner else _brief_fallback(session),
            my_nickname=session.my_nickname,
            status=session.status,
            messages=[
                PracticeMessageItem(index=m.index, role=m.role, content=m.content, created_at=m.created_at)
                for m in session.messages
            ],
            created_at=session.created_at,
        )

    # 세션을 ended 로 닫고 최신 상태를 응답으로 돌려준다
    async def end(self, session: PracticeSession) -> PracticeSessionResponse:
        await self.repo.end_session(session)
        return await self.get(session)

    # ── 스트리밍 ──────────────────────────────────────────

    # 상대(+나) 페르소나 프로필을 불러와 이번 대화의 시스템 프롬프트를 만든다
    async def _system(self, session: PracticeSession) -> str:
        partner = await self._load("partner", PersonaRef(persona_id=session.partner_persona_id))
        me = None
        if session.my_persona_id:
            me = await load_persona(self.db, PersonaRef(persona_id=session.my_persona_id))
        return self.agent.system_prompt(
            partner_name=session.partner_nickname,
            partner=partner.response,
            my_name=session.my_nickname,
            me=me.response if me else None,
        )

    # 세션의 최근 메시지들을 LLM 에 넘길 messages 형식으로 변환
    @staticmethod
    def _history(session: PracticeSession) -> list[dict]:
        """DB 메시지 → LLM 메시지. 최근 HISTORY_WINDOW 개만.

        Anthropic 은 첫 메시지가 user 여야 한다. 상대가 먼저 인사한 세션은 첫 줄이 assistant 라,
        그 앞에 인사 지시문을 user 로 끼운다 — 인사말을 버리면 "아까 러닝 얘기" 같은 맥락이 끊긴다."""
        msgs = [
            {"role": "user" if m.role == "user" else "assistant", "content": m.content}
            for m in session.messages[-HISTORY_WINDOW:]
        ]
        if msgs and msgs[0]["role"] == "assistant":
            msgs.insert(0, {"role": "user", "content": OPENING_INSTRUCTION})
        return msgs

    # 상대가 먼저 말을 거는 첫 답변을 스트리밍한다
    async def stream_opening(self, session: PracticeSession) -> AsyncIterator[Event]:
        """상대가 먼저 말을 건다. 이미 메시지가 있으면 그냥 다음 답변으로 취급."""
        if session.status != "active":
            raise SessionEnded(session.id)
        async for ev in self._respond(session, opening=not session.messages):
            yield ev

    # 내 메시지를 먼저 저장한 뒤 상대 답변을 스트리밍한다
    async def stream_reply(self, session: PracticeSession, message: str) -> AsyncIterator[Event]:
        if session.status != "active":
            raise SessionEnded(session.id)
        await self.repo.add_message(session, "user", message)
        async for ev in self._respond(session, opening=False):
            yield ev

    # LLM 스트리밍 응답을 만들어 delta 로 흘리고, 끝나면 저장·커밋 후 done 을 낸다.
    # 스트림이 끊기면 롤백 후 error, 첫 조각도 못 받으면 폴백 문장으로 대화를 이어간다
    async def _respond(self, session: PracticeSession, *, opening: bool) -> AsyncIterator[Event]:
        system = await self._system(session)
        history = self._history(session)
        index = session.message_count
        yield "start", StartEvent(session_id=session.id, message_index=index)

        parts: list[str] = []
        source = "llm"
        try:
            async for chunk in self.agent.reply(system=system, history=history, opening=opening):
                parts.append(chunk)
                yield "delta", DeltaEvent(text=chunk)
        except LLMError as e:
            if parts:
                # 중간에 끊겼다. 반 토막 문장을 저장하면 다음 턴 맥락이 망가진다 — 버리고 알린다
                logger.warning("practice stream broke mid-reply: %s", e)
                await self.db.rollback()
                yield "error", ErrorEvent(detail=f"답변 도중 연결이 끊겼어요: {e}")
                return
            # 첫 조각도 못 받았다. 대화가 멈추지 않게 폴백 한 줄
            logger.warning("practice reply failed (%s), using fallback", e)
            source = "fallback"
            parts = [FALLBACK_REPLY]
            yield "delta", DeltaEvent(text=FALLBACK_REPLY)

        content = "".join(parts).strip()
        await self.repo.add_message(session, "persona", content, source)
        await self.db.commit()
        yield "done", DoneEvent(session_id=session.id, message_index=index, content=content, source=source)


# 상대 페르소나 레코드가 지워졌을 때, 세션에 남은 값만으로 최소한의 브리핑을 만든다
def _brief_fallback(session: PracticeSession):
    from app.features.persona.schemas import PersonaBrief

    return PersonaBrief(
        persona_id=session.partner_persona_id,
        user_id=session.partner_user_id,
        nickname=session.partner_nickname,
    )
