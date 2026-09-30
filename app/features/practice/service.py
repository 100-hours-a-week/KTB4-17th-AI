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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.guardrail import GuardrailContext, apply_text, effective_mode, practice_cove_addon
from app.core.guardrail_trace import record_guardrail
from app.core.observability import build_langfuse_metadata
from app.features.persona.lookup import LoadedPersona, load_persona
from app.features.persona.schemas import PersonaRef

from .agents import (
    FALLBACK_REPLY,
    OPENING_INSTRUCTION,
    LLMError,
    PartnerAgent,
    without_identity_confession,
)
from .models import PracticeSession
from .repository import PracticeRepository
from .schemas import (
    HISTORY_WINDOW,
    DeltaEvent,
    DoneEvent,
    ErrorEvent,
    PracticeMessageItem,
    PracticeSessionResponse,
    PracticeSessionSummary,
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


class NothingToRetry(Exception):
    """다시 받을 답변이 없다 — 마지막 메시지가 이미 상대 답변이거나 대화가 비어 있다."""


class OpeningAlreadyDone(Exception):
    """상대의 첫 인사는 세션당 한 번 — 이미 메시지가 있으면 다시 열 수 없다."""


CONCURRENT_DETAIL = "다른 요청을 처리 중이에요. 잠시 뒤 다시 시도해 주세요"


class ReplyFailed(Exception):
    """답변 스트림이 도중에 끊겼다 (일반 JSON 응답으로 모을 때만 쓴다)."""


class ConcurrentRequest(Exception):
    """같은 세션에 동시에 들어온 요청과 메시지 index 가 겹쳤다 — 늦게 커밋한 쪽이 진다."""


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
        partner = await self._load("partner", req.partner_ref())
        me_ref = req.me_ref()
        me = await self._load("me", me_ref) if me_ref else None
        # 내 페르소나가 있으면 온보딩 닉네임이 우선. nickname 은 페르소나가 없는 사용자용이다
        my_nickname = me.nickname if me else (req.nickname or "회원")

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

    # 내(user_id) 연습대화 목록. 상대 정보는 세션에 고정된 페르소나 버전 기준
    async def list_for_user(self, user_id: str, limit: int) -> list[PracticeSessionSummary]:
        summaries = []
        for s in await self.repo.list_for_user(user_id, limit):
            partner = await load_persona(self.db, PersonaRef(persona_id=s.partner_persona_id))
            summaries.append(
                PracticeSessionSummary(
                    session_id=s.id,
                    partner=partner.brief if partner else _brief_fallback(s),
                    my_nickname=s.my_nickname,
                    status=s.status,
                    message_count=s.message_count,
                    created_at=s.created_at,
                    updated_at=s.updated_at,
                )
            )
        return summaries

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

    # 메시지를 저장·커밋하되 (session_id, index) 유니크 위반(flush/commit 어느 쪽이든)이면 롤백하고 ConcurrentRequest 로 바꾼다
    async def _save_message(self, session: PracticeSession, role: str, content: str, source: str | None = None) -> None:
        try:
            await self.repo.add_message(session, role, content, source)
            await self.db.commit()
        except IntegrityError as e:
            await self.db.rollback()
            raise ConcurrentRequest from e

    # 상대(+나) 페르소나 프로필을 불러와 이번 대화의 시스템 프롬프트를 만든다
    async def _system(self, session: PracticeSession) -> str:
        partner = await self._load("partner", PersonaRef(persona_id=session.partner_persona_id))
        me = None
        if session.my_persona_id:
            me = await load_persona(self.db, PersonaRef(persona_id=session.my_persona_id))
        mode = effective_mode(session.user_id)
        system = self.agent.system_prompt(
            partner_name=session.partner_nickname,
            partner=partner.response,
            my_name=session.my_nickname,
            me=me.response if me else None,
        )
        if mode == "enforce":
            system += "\n" + practice_cove_addon(
                self._guardrail_context(session, partner.response, me.response if me else None)
            )
        return system

    @staticmethod
    def _guardrail_context(session: PracticeSession, partner, me) -> GuardrailContext:
        speaker = [*partner.interests, *partner.routine, *partner.date_prefer]
        own = [*me.interests, *me.routine, *me.date_prefer] if me else []
        partner_only = [item for item in own if item not in speaker]
        user_texts = [m.content for m in session.messages if m.role == "user"]
        return GuardrailContext(
            surface="practice_reply",
            speaker_name=session.partner_nickname,
            partner_name=session.my_nickname,
            speaker_attributes=speaker,
            partner_attributes=partner_only,
            user_texts=user_texts,
            task="single_turn",
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
        """상대가 먼저 말을 건다. 이미 메시지가 있으면 OpeningAlreadyDone."""
        if session.status != "active":
            raise SessionEnded(session.id)
        if session.messages:
            raise OpeningAlreadyDone(session.id)
        async for ev in self._respond(session, opening=True):
            yield ev

    # 내 메시지를 먼저 커밋한 뒤 상대 답변을 스트리밍한다.
    # 답변이 도중에 끊겨도 내 메시지는 남는다 — 새로고침해도 보이고, /retry 로 답변만 다시 받는다
    async def stream_reply(self, session: PracticeSession, message: str) -> AsyncIterator[Event]:
        if session.status != "active":
            raise SessionEnded(session.id)
        await self._save_message(session, "user", message)
        async for ev in self._respond(session, opening=False):
            yield ev

    # 답을 못 받은 내 마지막 메시지에 상대 답변만 다시 받는다 (메시지를 다시 보내지 않는다)
    async def stream_retry(self, session: PracticeSession) -> AsyncIterator[Event]:
        if session.status != "active":
            raise SessionEnded(session.id)
        if not session.messages or session.messages[-1].role != "user":
            raise NothingToRetry(session.id)
        async for ev in self._respond(session, opening=False):
            yield ev

    # LLM 스트리밍 응답을 만들어 delta 로 흘리고, 끝나면 저장·커밋 후 done 을 낸다.
    # 스트림이 끊기면 롤백 후 error, 첫 조각도 못 받으면 폴백 문장으로 대화를 이어간다
    async def _respond(self, session: PracticeSession, *, opening: bool) -> AsyncIterator[Event]:
        mode = effective_mode(session.user_id)
        system = await self._system(session)
        history = self._history(session)
        index = session.message_count
        yield "start", StartEvent(session_id=session.id, message_index=index)

        parts: list[str] = []
        source = "llm"
        validation = None
        try:
            async for chunk in self.agent.reply(
                system=system,
                history=history,
                opening=opening,
                trace_metadata=build_langfuse_metadata(
                    feature="practice",
                    operation="reply",
                    user_id=session.user_id,
                    session_id=session.id,
                    tags=("streaming",),
                    messageIndex=index,
                    opening=opening,
                ),
            ):
                parts.append(chunk)
                if mode != "enforce":
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
        if source == "llm" and mode != "off":
            partner = await self._load("partner", PersonaRef(persona_id=session.partner_persona_id))
            me = (
                await load_persona(self.db, PersonaRef(persona_id=session.my_persona_id))
                if session.my_persona_id
                else None
            )
            ctx = self._guardrail_context(session, partner.response, me.response if me else None)

            async def regenerate(notice: str) -> str:
                retry_history = [
                    *history,
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": notice},
                ]
                chunks = []
                async for chunk in self.agent.reply(
                    system=system,
                    history=retry_history,
                    opening=False,
                    trace_metadata=build_langfuse_metadata(
                        feature="practice",
                        operation="reply",
                        user_id=session.user_id,
                        session_id=session.id,
                        tags=("guardrail-retry",),
                        messageIndex=index,
                    ),
                ):
                    chunks.append(chunk)
                return "".join(chunks).strip()

            applied = await apply_text(
                content, ctx, user_key=session.user_id, regenerate=regenerate, fallback=FALLBACK_REPLY
            )
            validation = applied.result
            content = applied.text
            if validation and validation.status == "FALLBACK":
                source = "fallback"
            content = without_identity_confession(content, session.partner_nickname)
            await record_guardrail(
                self.db,
                feature="practice",
                operation="reply",
                session_id=session.id,
                user_id=session.user_id,
                mode=mode,
                result=validation,
                initial_text="".join(parts).strip(),
            )
            if mode == "enforce":
                yield "delta", DeltaEvent(text=content)
        else:
            content = without_identity_confession(content, session.partner_nickname)
        await self._save_message(session, "persona", content, source)
        yield (
            "done",
            DoneEvent(
                session_id=session.id,
                message_index=index,
                content=content,
                source=source,
                validationResult=validation,
            ),
        )


# 스트림 이벤트를 끝까지 소비해 최종 done 이벤트를 돌려준다 — 일반(JSON) 라우트용.
# error 이벤트(도중 끊김)는 ReplyFailed 로, 스트림 안에서 나는 도메인 예외는 그대로 올라간다
async def collect_reply(events: AsyncIterator[Event]) -> DoneEvent:
    async for _, data in events:
        if isinstance(data, ErrorEvent):
            raise ReplyFailed(data.detail)
        if isinstance(data, DoneEvent):
            return data
    raise ReplyFailed("답변이 만들어지지 않았어요")


# 상대 페르소나 레코드가 지워졌을 때, 세션에 남은 값만으로 최소한의 브리핑을 만든다
def _brief_fallback(session: PracticeSession):
    from app.features.persona.schemas import PersonaBrief

    return PersonaBrief(
        persona_id=session.partner_persona_id,
        user_id=session.partner_user_id,
        nickname=session.partner_nickname,
    )
