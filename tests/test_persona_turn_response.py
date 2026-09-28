import asyncio
from types import SimpleNamespace

from app.features.persona.agents import Utterance
from app.features.persona.schemas import Segment, TurnResponse
from app.features.persona.service import OnboardingService

TYPES = ["intro", "reason", "question", "self_disclosure", "answer_prompt"]


def test_turn_response_without_segments_is_backward_compatible():
    r = TurnResponse(session_id="s", utterance="안녕", progress="1/5")
    assert r.model_dump()["utterance"] == "안녕"
    assert r.segments == []


def _service(utterance):
    svc = OnboardingService.__new__(OnboardingService)

    async def generate(**kwargs):
        return utterance

    async def add_question(*args):
        return None

    svc.conversation = SimpleNamespace(generate=generate)
    svc.repo = SimpleNamespace(add_question=add_question)
    return svc


def _session(**kw):
    base = dict(id="s1", coverage={}, turn_index=0, total_turns=5, used_topic_ids=[], nickname="민수", turns=[])
    base.update(kw)
    return SimpleNamespace(**base)


def test_first_response_serializes_utterance_and_five_segments():
    segs = tuple(Segment(type=t, text=f"{t}!") for t in TYPES)
    u = Utterance(text=" ".join(s.text for s in segs), source="llm", segments=segs)
    r = asyncio.run(_service(u)._ask_next(_session()))
    data = r.model_dump()
    assert data["utterance"] == u.text
    assert [s["type"] for s in data["segments"]] == TYPES
    assert " ".join(s["text"] for s in data["segments"]) == data["utterance"]


def test_done_response_has_closing_segment():
    r = asyncio.run(_service(None)._ask_next(_session(turn_index=5)))
    data = r.model_dump()
    assert data["done"] is True
    assert data["segments"] == [{"type": "closing", "text": data["utterance"]}]
