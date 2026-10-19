import asyncio

from app.features.practice.agents import PartnerAgent
from app.features.practice.schemas import PracticeStartRequest
from app.v2.persona.repository import PersonaMemoryRepository
from app.v2.practice.memory import MemoryContext, load_memory_context, render_memory_section
from app.v2.practice.service import V2PracticeService
from tests.conftest import seed_persona, with_db

VEC = [1.0, 0.0] + [0.0] * 1022


class FakeEmbedder:
    async def encode_query(self, text: str) -> list[float]:
        return VEC

    async def encode(self, text: str) -> list[float]:
        return VEC

    async def encode_batch(self, texts: list[str]) -> list[list[float]]:
        return [VEC for _ in texts]


class CapturingAgent(PartnerAgent):
    """LLM 호출(reply)만 가짜로 바꾼다. 시스템 프롬프트 조립(system_prompt)은 v1 그대로 쓰고 결과를 붙잡아 둔다."""

    def __init__(self) -> None:
        self.systems: list[str] = []

    async def reply(self, *, system, history, opening=False, trace_metadata=None):
        self.systems.append(system)
        yield "부산에서 주말마다 등산 다니시는군요!"


async def _seed_memory(db_session) -> None:
    repo = PersonaMemoryRepository(db_session)
    await repo.update_core_profile("u1", {"region": "부산", "job": "개발자"})
    await repo.add_fact(
        user_id="u1",
        category="취미",
        fact="주말마다 등산을 다닌다",
        confidence=0.92,
        status="confirmed",
        embedding=VEC,
        embedding_model="KURE-v1",
    )
    await db_session.commit()


def test_load_memory_context_core_always_facts_only_for_real_utterance():
    async def body(factory):
        async with factory() as db_session:
            await _seed_memory(db_session)
            emb = FakeEmbedder()

            full = await load_memory_context(db_session, user_id="u1", query="이번 주말에 산에 갈까 해요", embedder=emb)
            assert full.core_profile["region"] == "부산" and len(full.facts) == 1

            trivial = await load_memory_context(db_session, user_id="u1", query="ㅋㅋㅋㅋ", embedder=emb)
            assert trivial.core_profile["region"] == "부산" and trivial.facts == []

            opening = await load_memory_context(db_session, user_id="u1", query=None, embedder=emb)
            assert opening.facts == []

            section = render_memory_section("민수", full)
            assert "거주 지역: 부산" in section and "주말마다 등산을 다닌다" in section
            assert "아직 알려진 프로필 없음" in render_memory_section("민수", MemoryContext(facts=full.facts))

    asyncio.run(with_db(body))


def test_stream_reply_injects_memory_into_system_prompt_and_grounds_guardrail():
    async def body(factory):
        async with factory() as db_session:
            await seed_persona(db_session, persona_id="p-partner", user_id="pu1", nickname="지민")
            await seed_persona(db_session, persona_id="p-me", user_id="u1", nickname="민수")
            await _seed_memory(db_session)

            service = V2PracticeService(db_session, embedder=FakeEmbedder())
            agent = CapturingAgent()
            service.agent = agent
            started = await service.start(PracticeStartRequest(partner_user_id="pu1", me_user_id="u1"))
            await db_session.commit()

            session = await service.repo.get_session(started.session_id)
            events = [name async for name, _ in service.stream_reply(session, "이번 주말에 산에 갈까 해요")]
            assert events[0] == "start" and events[-1] == "done"

            system = agent.systems[0]
            assert "거주 지역: 부산" in system
            assert "주말마다 등산을 다닌다" in system

            # 가드레일 근거: 검색된 Fact 와 코어 값이 user_texts 에 들어간다
            ctx = service._guardrail_context(
                session, (await service._load("partner", _ref("p-partner"))).response, None
            )
            assert "주말마다 등산을 다닌다" in ctx.user_texts and "부산" in ctx.user_texts

    asyncio.run(with_db(body))


def test_session_without_user_id_skips_memory():
    async def body(factory):
        async with factory() as db_session:
            await seed_persona(db_session, persona_id="p-partner", user_id="pu1", nickname="지민")
            await _seed_memory(db_session)

            service = V2PracticeService(db_session, embedder=FakeEmbedder())
            agent = CapturingAgent()
            service.agent = agent
            started = await service.start(PracticeStartRequest(partner_user_id="pu1", nickname="손님"))
            await db_session.commit()

            session = await service.repo.get_session(started.session_id)
            assert session.user_id is None
            _ = [e async for e in service.stream_reply(session, "이번 주말에 산에 갈까 해요")]
            assert "주말마다 등산을 다닌다" not in agent.systems[0]

    asyncio.run(with_db(body))


def _ref(persona_id: str):
    from app.features.persona.schemas import PersonaRef

    return PersonaRef(persona_id=persona_id)
