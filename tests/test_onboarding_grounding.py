"""실제 Conversation/Extraction 호출 경계에서 화자와 사용자 활동이 보존되는지 확인한다."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.features.persona import agents
from app.features.persona.agents import ConversationAgent, ExtractionAgent, LLMError
from app.features.persona.schemas import TOPICS


def topic(name):
    return next(item for item in TOPICS if item.id == name)


@pytest.mark.parametrize("answer", ["잔다", "저는 TV보면서 누워있어요", "빵 먹는 걸 좋아해요"])
def test_next_topic_instruction_never_replaces_the_actual_user_answer(monkeypatch, answer):
    history = [
        {"role": "assistant", "content": "저는 빵을 만들어요. 주말엔 보통 어떻게 보내세요?"},
        {"role": "user", "content": answer},
    ]
    captured = []

    async def fake_call(**kwargs):
        captured.append(kwargs)
        return "편히 쉬는 시간이 좋으시군요. 사람을 볼 때 어떤 부분을 먼저 보세요?"

    monkeypatch.setattr(agents, "_call", fake_call)
    asyncio.run(
        ConversationAgent().generate(
            history=history,
            topic=topic("contact"),
            turn_index=2,
            total_turns=10,
            nickname="테스트",
        )
    )
    request = captured[0]
    assert request["messages"][-1] == {"role": "user", "content": answer}
    assert [m["content"] for m in request["messages"] if m["role"] == "user"] == [answer]


def test_weekend_first_turn_failure_does_not_inject_a_baking_hobby(monkeypatch):
    async def fail(**kwargs):
        raise LLMError("provider unavailable")

    monkeypatch.setattr(agents, "_call", fail)
    result = asyncio.run(
        ConversationAgent().generate(
            history=[],
            topic=topic("weekend"),
            turn_index=0,
            total_turns=10,
            nickname="테스트",
        )
    )
    assert result.source == "seed"
    assert "베이킹" not in result.text and "빵을 만들" not in result.text


@pytest.mark.parametrize(
    "answer", ["잔다", "취미는 딱히 없어요", "빵 먹는 걸 좋아해요", "빵은 사 먹고 만들지는 않아요"]
)
def test_extraction_evidence_contains_only_the_users_own_activity(monkeypatch, answer):
    captured = []

    async def fake_call(**kwargs):
        captured.append(kwargs)
        return "{}"

    monkeypatch.setattr(agents, "_call", fake_call)
    asyncio.run(
        ExtractionAgent().extract(
            [
                {"role": "assistant", "content": "저는 주말에 베이킹을 하고 빵을 만들어요. 주말엔요?"},
                {"role": "user", "content": answer},
            ]
        )
    )
    payload = json.loads(captured[0]["messages"][0]["content"])
    assert payload["user_evidence"] == [answer]
    assert "베이킹" not in json.dumps(payload["user_evidence"], ensure_ascii=False)
    assert "빵을 만들어요" not in json.dumps(payload["user_evidence"], ensure_ascii=False)


def test_later_turn_keeps_the_three_part_frame_without_a_new_hobby():
    instruction = ConversationAgent._instruction(topic("weekend"), 2, 10, "테스트")
    assert "매 턴 아래 셋을 자연스럽게 섞습니다" in agents.SYSTEM_PROMPT
    assert "내 얘기 한 줄" in agents.SYSTEM_PROMPT
    assert "필요할 때만" not in agents.SYSTEM_PROMPT
    assert "문 여는 한 줄" in instruction
    assert "한 문장 이내로 덧붙이세요" in instruction
    assert "질문을 정확히 하나만" in instruction
    assert "필요한 경우에만" not in instruction
    assert "베이킹" not in instruction and "빵을 만들" not in instruction


def test_guardrail_regeneration_keeps_the_actual_answer_as_the_last_user_message(monkeypatch):
    history = [
        {"role": "assistant", "content": "주말엔 보통 뭐 하세요?"},
        {"role": "user", "content": "잔다"},
    ]
    requests = []

    async def fake_call(**kwargs):
        requests.append(kwargs)
        return "주말에는 편하게 쉬시는군요. 연락은 어느 정도가 편하세요?"

    async def regenerate_once(text, ctx, **kwargs):
        result = await kwargs["regenerate"]("사용자 답변을 바꾸어 해석하지 말 것")
        return SimpleNamespace(text=result, result=None)

    monkeypatch.setattr(agents, "_call", fake_call)
    monkeypatch.setattr(agents, "apply_text", regenerate_once)
    asyncio.run(
        ConversationAgent().generate(
            history=history,
            topic=topic("contact"),
            turn_index=2,
            total_turns=10,
            nickname="테스트",
        )
    )
    assert len(requests) == 2
    for request in requests:
        assert request["messages"][-1] == history[-1]
        assert [m["content"] for m in request["messages"] if m["role"] == "user"] == ["잔다"]
    assert "사용자 답변을 바꾸어 해석하지 말 것" in requests[1]["system"]


def test_extraction_preserves_each_users_own_answers_and_pairs_question_context():
    first = json.loads(
        ExtractionAgent._transcript(
            [
                {"role": "assistant", "content": "저는 빵을 만들어요. 취미가 있으세요?"},
                {"role": "user", "content": "빵 먹기와 독서를 좋아해요"},
                {"role": "assistant", "content": "주말엔요?"},
                {"role": "user", "content": "그냥 쉬어요"},
            ]
        )
    )
    second = json.loads(
        ExtractionAgent._transcript(
            [
                {"role": "assistant", "content": "취미가 있으세요?"},
                {"role": "user", "content": "딱히 없어요"},
            ]
        )
    )
    assert first["user_evidence"] == ["빵 먹기와 독서를 좋아해요", "그냥 쉬어요"]
    assert second["user_evidence"] == ["딱히 없어요"]
    assert first["question_context"][1] == {"answer_index": 1, "haru_context_only": ["주말엔요?"]}
