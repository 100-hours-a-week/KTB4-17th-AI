import asyncio
from types import SimpleNamespace

import pytest

from app.features.persona.schemas import PersonaResponse
from app.features.practice import agents
from app.features.practice.agents import OPENING_INSTRUCTION, LLMError, PartnerAgent


def _chunk(text):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


def _fake_client(monkeypatch, chunks=(), error=None):
    """OpenAI 호환 클라이언트(외부 경계)만 가짜로. 조각 처리·에러 변환은 진짜 _stream 이 한다."""
    seen = {}

    async def create(**kwargs):
        seen.update(kwargs)
        if error:
            raise error

        async def stream():
            for c in chunks:
                yield c

        return stream()

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(agents, "_get_client", lambda: client)
    return seen


async def _collect(gen):
    return [x async for x in gen]


def _reply(history, opening=False):
    return asyncio.run(_collect(PartnerAgent().reply(system="SYS", history=history, opening=opening)))


def test_reply_streams_only_non_empty_text_chunks(monkeypatch):
    _fake_client(monkeypatch, [_chunk("안녕"), _chunk(None), _chunk(""), SimpleNamespace(choices=[]), _chunk("하세요")])

    assert _reply([{"role": "user", "content": "hi"}]) == ["안녕", "하세요"]


def test_system_prompt_goes_first_then_history(monkeypatch):
    seen = _fake_client(monkeypatch, [_chunk("네")])
    history = [{"role": "user", "content": "주말에 뭐 해요?"}]

    _reply(history)

    assert seen["messages"] == [{"role": "system", "content": "SYS"}, *history]
    assert seen["stream"] is True
    assert seen["stream_options"] == {"include_usage": True}
    assert seen["max_tokens"] == 300
    assert seen["name"] == "practice-reply"


def test_opening_appends_instruction_as_user_message(monkeypatch):
    seen = _fake_client(monkeypatch, [_chunk("안녕하세요")])

    _reply([], opening=True)

    assert seen["messages"][-1] == {"role": "user", "content": OPENING_INSTRUCTION}


def test_provider_error_becomes_llm_error(monkeypatch):
    _fake_client(monkeypatch, error=RuntimeError("401 invalid api key"))

    with pytest.raises(LLMError, match="401 invalid api key"):
        _reply([{"role": "user", "content": "hi"}])


def test_system_prompt_mentions_me_only_when_my_persona_exists():
    partner = PersonaResponse(persona_id="p", scores={}, interests=["러닝"])
    me = PersonaResponse(persona_id="m", scores={}, interests=["보드게임"])

    without_me = PartnerAgent.system_prompt(partner_name="지수", partner=partner, my_name="민수", me=None)
    with_me = PartnerAgent.system_prompt(partner_name="지수", partner=partner, my_name="민수", me=me)

    assert "러닝" in without_me
    assert "민수님에 대해 참고할 것" not in without_me
    assert "민수님에 대해 참고할 것" in with_me
    assert "보드게임" in with_me
    assert "연기하는 AI" not in without_me
    assert '당신은 "지수" 본인입니다' in without_me
    assert "제3자로 부르지 않습니다" in without_me
