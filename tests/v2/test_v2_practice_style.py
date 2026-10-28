"""v2 연습대화는 말 놓기를 제안·판정·저장하지 않는다."""

import asyncio

from app.core.db import Base
from app.features.practice.schemas import PracticeStartRequest
from app.v2.persona.models import UserPersonaCore
from app.v2.practice.agents import PartnerAgent
from app.v2.practice.service import V2PracticeService
from tests.conftest import seed_persona, with_db

VEC = [1.0, 0.0] + [0.0] * 1022
ASK_PHRASE = "말 편하게 해도 될까요?"


class FakeEmbedder:
    async def encode_query(self, text):
        return VEC

    async def encode(self, text):
        return VEC

    async def encode_batch(self, texts):
        return [VEC for _ in texts]


class ScriptedPartner(PartnerAgent):
    def __init__(self):
        self.systems: list[str] = []

    async def reply(self, *, system, history, opening=False, trace_metadata=None):
        self.systems.append(system)
        yield "오 그렇군요 ㅎㅎ"


async def _seed(db):
    await seed_persona(db, persona_id="p-partner", user_id="pu1", nickname="지민")
    await seed_persona(db, persona_id="p-me", user_id="u1", nickname="민수")


def test_practice_never_adds_speech_permission_prompt_after_many_turns():
    agent = ScriptedPartner()

    async def body(factory):
        async with factory() as db:
            await _seed(db)
            started = await V2PracticeService(db, embedder=FakeEmbedder()).start(
                PracticeStartRequest(partner_user_id="pu1", me_user_id="u1")
            )
            await db.commit()

        for _ in range(12):
            async with factory() as db:
                service = V2PracticeService(db, embedder=FakeEmbedder())
                service.agent = agent
                session = await service.repo.get_session(started.session_id)
                _ = [event async for event in service.stream_reply(session, "오늘 뭐 하셨어요?")]

    asyncio.run(with_db(body))

    assert len(agent.systems) == 12
    assert all(ASK_PHRASE not in system for system in agent.systems)
    assert all("말투 허락" not in system and "말투 고정" not in system for system in agent.systems)


def test_speech_permission_state_is_not_part_of_runtime_metadata():
    assert "v2_practice_speech_state" not in Base.metadata.tables
    assert not hasattr(UserPersonaCore, "casual_speech_declined_at")
