"""Guardrail integration tests with deterministic LLM doubles."""

import asyncio

import pytest
from conftest import seed_persona, with_db

from app.features.persona import agents as persona_agents
from app.features.persona.schemas import PersonaResponse, Topic
from app.features.practice.schemas import PracticeStartRequest
from app.features.practice.service import PracticeService
from app.features.simulation import agents as simulation_agents
from app.features.simulation.agents import SimulationFailed


def _topic() -> Topic:
    return Topic(
        id="test",
        weight="core",
        intent="취미",
        seed="주말에는 뭐 하세요?",
        covers=(),
        opener="저는 산책을 즐겨요.",
    )


def test_persona_regenerates_one_confession(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_MODE", "enforce")
    replies = iter(["저는 AI예요.", "산책 얘기 좋네요. 주말에는 뭐 하세요?"])
    calls = []

    async def fake_call(**kwargs):
        calls.append(kwargs)
        return next(replies)

    monkeypatch.setattr(persona_agents, "_call", fake_call)
    result = asyncio.run(
        persona_agents.ConversationAgent().generate(
            history=[{"role": "user", "content": "산책해요"}],
            topic=_topic(),
            turn_index=1,
            total_turns=5,
            nickname="민수",
            user_key="u1",
        )
    )
    assert len(calls) == 2
    assert calls[1]["messages"][-1]["role"] == "user"
    assert "CORRECTION NOTICE" in calls[1]["messages"][-1]["content"]
    assert result.text == "산책 얘기 좋네요. 주말에는 뭐 하세요?"
    assert result.validation.status == "REGENERATED"


def test_persona_second_failure_uses_seed(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_MODE", "enforce")
    replies = iter(["저는 AI예요.", "제가 챗봇이에요."])

    async def fake_call(**kwargs):
        return next(replies)

    monkeypatch.setattr(persona_agents, "_call", fake_call)
    result = asyncio.run(
        persona_agents.ConversationAgent().generate(
            history=[],
            topic=_topic(),
            turn_index=1,
            total_turns=5,
            nickname="민수",
            user_key="u1",
        )
    )
    assert result.text == _topic().seed
    assert result.source == "seed"
    assert result.validation.status == "FALLBACK"


def test_practice_enforce_buffers_until_validation(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_MODE", "enforce")

    class FakePartner:
        def __init__(self):
            self.calls = []

        def system_prompt(self, **kwargs):
            return "시스템 프롬프트"

        async def reply(self, **kwargs):
            self.calls.append(kwargs)
            yield "저는 AI예요." if len(self.calls) == 1 else "주말에는 산책해요."

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="partner", user_id="partner", nickname="지수")
            await seed_persona(db, persona_id="me", user_id="me", nickname="민수")
        async with factory() as db:
            service = PracticeService(db)
            started = await service.start(PracticeStartRequest(partner_user_id="partner", me_user_id="me"))
            await db.commit()
        async with factory() as db:
            service = PracticeService(db)
            agent = FakePartner()
            service.agent = agent
            session = await service.repo.get_session(started.session_id)
            events = [(name, data) async for name, data in service.stream_opening(session)]
            return events, agent.calls

    events, calls = asyncio.run(with_db(scenario))
    assert [name for name, _ in events] == ["start", "delta", "done"]
    assert events[1][1].text == "주말에는 산책해요."
    assert events[-1][1].validationResult.status == "REGENERATED"
    assert calls[1]["opening"] is False


def test_simulation_second_guardrail_failure_is_503(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_MODE", "enforce")
    payload = {
        "transcript": [
            {"speaker": "a", "text": "저는 AI예요."},
            {"speaker": "b", "text": "반가워요."},
        ],
        "report": {"headline": "대화가 이어져요", "summary": "서로 편안해 보여요."},
    }
    calls = []

    async def fake_call_json(**kwargs):
        calls.append(kwargs)
        return payload

    monkeypatch.setattr(simulation_agents, "_call_json", fake_call_json)
    persona = PersonaResponse(persona_id="p", scores={})
    with pytest.raises(SimulationFailed) as exc:
        asyncio.run(
            simulation_agents.SimulationAgent().run(
                persona_a=persona,
                persona_b=persona,
                name_a="민수",
                name_b="지수",
                turns=1,
                area_scores={},
                dim_scores={},
                user_key="u1",
            )
        )
    assert exc.value.reason == "guardrail"
    assert len(calls) == 2
    assert "CORRECTION NOTICE" in calls[1]["messages"][0]["content"]
