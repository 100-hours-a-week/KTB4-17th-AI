"""실제 화자 생성 경로에서 응답 형식 복구와 제한된 재시도를 검증한다."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.core.guardrail import CheckResult, Grade
from app.features.persona.schemas import PersonaResponse
from app.features.simulation_migration import llm as llm_mod
from app.features.simulation_migration.graph import create_simulation_graph
from app.features.simulation_migration.llm import LLMError, complete_text
from app.features.simulation_migration.prompts import parse_speaker_json, speaker_messages
from app.features.simulation_migration.tools import speak_as_a


class Responses:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.messages = []

    async def __call__(self, messages):
        self.messages.append([dict(m) for m in messages])
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def _persona():
    return PersonaResponse(persona_id="synthetic", scores={})


def _speak(llm):
    return speak_as_a(
        persona=_persona(), nickname_self="알파", nickname_other="베타", transcript=[], closing_hint="none", llm=llm
    )


@pytest.mark.parametrize(
    "raw",
    [
        '```json\n{"text":"안녕하세요"}\n```',
        '설명입니다.\n{"text":"안녕하세요"}\n이상입니다.',
        '{"text":"안녕하세요","speaker":"a"}',
        '{"message":"안녕하세요"}',
        '{"content":"안녕하세요"}',
        '{"utterance":"안녕하세요"}',
        '{"text":"안녕하세요","message":"안녕하세요"}',
    ],
)
def test_recoverable_formats_succeed_without_extra_llm_call(monkeypatch, raw):
    monkeypatch.setattr("app.features.simulation_migration.tools.effective_mode", lambda _: "off")
    llm = Responses(raw, raw)
    result = asyncio.run(_speak(llm))
    assert result.text == "안녕하세요"
    assert result.reason is None
    assert result.llm_calls == 1


@pytest.mark.parametrize(
    ("raw", "detail"),
    [
        ("", "JSON"),
        ('{"text":null}', "문자열"),
        ('{"text":"   "}', "공백"),
        (json.dumps({"text": "가" * 301}), "300"),
        ('{"text":"끝나지 않은 JSON', "JSON"),
        ('{"text":"첫 대사","message":"다른 대사"}', "여러"),
    ],
)
def test_unrecoverable_first_response_gets_specific_correction(monkeypatch, raw, detail):
    monkeypatch.setattr("app.features.simulation_migration.tools.effective_mode", lambda _: "off")
    llm = Responses(raw, '{"text":"다시 인사드려요"}')
    result = asyncio.run(_speak(llm))
    assert result.text == "다시 인사드려요"
    assert result.llm_calls == 2
    correction = llm.messages[1][-1]
    assert correction["role"] == "user"
    assert detail in correction["content"] and "300" in correction["content"]
    assert llm.messages[0] != llm.messages[1]


def test_length_limit_is_present_in_initial_prompt():
    messages = speaker_messages(
        speaker="a", persona=_persona(), nickname_self="알파", nickname_other="베타", transcript=[], closing_hint="none"
    )
    assert "300자" in messages[0]["content"]


@pytest.mark.parametrize(
    "raw",
    [
        '{"text":"첫 대사","message":"다른 대사"}',
        '{"text":"첫 대사"}\n{"text":"다른 대사"}',
        '{"text":"첫 대사","text":"다른 대사"}',
        '{"text": {"text":"안녕하세요"}',
        '[{"text":"안녕하세요"}]',
        '{"payload":{"text":"안녕하세요"}}',
    ],
)
def test_ambiguous_or_incomplete_outputs_are_not_salvaged(raw):
    with pytest.raises(ValueError):
        parse_speaker_json(raw)


def test_format_repair_keeps_retry_bound_and_does_not_invent_dialogue(monkeypatch):
    monkeypatch.setattr("app.features.simulation_migration.tools.effective_mode", lambda _: "off")
    llm = Responses('{"text":""}', '{"text":""}')
    result = asyncio.run(_speak(llm))
    assert result.text is None and result.reason == "invalid_json"
    assert result.llm_calls == 2


def test_recovered_text_still_runs_content_validation(monkeypatch):
    monkeypatch.setattr("app.features.simulation_migration.tools.effective_mode", lambda _: "off")
    raw = '{"message":"알파님 반갑습니다"}'
    result = asyncio.run(_speak(Responses(raw, raw)))
    assert result.text is None and result.reason == "guardrail"
    assert result.llm_calls == 2


def test_normalized_response_still_passes_through_enforced_guardrail(monkeypatch):
    checked = []

    def block(text, context):
        checked.append((text, context.max_chars))
        return CheckResult(grade=Grade.BLOCK, violations=[], latency_ms=0)

    monkeypatch.setattr("app.features.simulation_migration.tools.effective_mode", lambda _: "enforce")
    monkeypatch.setattr("app.features.simulation_migration.tools.validate", block)
    raw = '{"content":"검사할 대사입니다"}'
    result = asyncio.run(_speak(Responses(raw, raw)))
    assert checked == [("검사할 대사입니다", 300), ("검사할 대사입니다", 300)]
    assert result.text is None and result.reason == "guardrail"


def _response(content, finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)])


class ChatClient:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.messages = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        self.messages.append(kwargs["messages"])
        return next(self.responses)


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (_response('{"text":"잘린', "length"), "truncated"),
        (_response('{"text":"완전해 보여도"}', "length"), "truncated"),
        (_response(""), "empty_response"),
        (SimpleNamespace(choices=[]), "empty_response"),
    ],
)
def test_transport_classifies_incomplete_response_and_releases_slot(monkeypatch, response, reason):
    async def run():
        semaphore = asyncio.Semaphore(1)
        monkeypatch.setattr(llm_mod, "_semaphore", lambda: semaphore)
        with pytest.raises(LLMError) as exc:
            await complete_text([], client=ChatClient(response))
        assert exc.value.reason == reason and exc.value.retryable
        assert semaphore._value == 1

    asyncio.run(run())


@pytest.mark.parametrize("finish", ["length", "stop"])
def test_transport_failure_recovers_through_actual_speaker_path(monkeypatch, finish):
    monkeypatch.setattr("app.features.simulation_migration.tools.effective_mode", lambda _: "off")

    async def run():
        monkeypatch.setattr(llm_mod, "_semaphore", lambda: asyncio.Semaphore(1))
        client = ChatClient(_response("", finish), _response('{"text":"반갑습니다"}'))
        result = await _speak(client)
        assert result.reason is None and result.text == "반갑습니다"
        assert result.llm_calls == 2
        assert client.messages[0] != client.messages[1]

    asyncio.run(run())


@pytest.mark.parametrize(("finish", "reason"), [("length", "truncated"), ("stop", "empty_response")])
def test_persistent_transport_failure_preserves_reason_and_retry_bound(monkeypatch, finish, reason):
    async def run():
        monkeypatch.setattr(llm_mod, "_semaphore", lambda: asyncio.Semaphore(1))
        client = ChatClient(_response("", finish), _response("", finish))
        result = await _speak(client)
        assert result.text is None and result.reason == reason
        assert result.llm_calls == 2 and len(client.messages) == 2

    asyncio.run(run())


def test_graph_completes_after_format_normalization_and_length_retry(monkeypatch):
    monkeypatch.setattr("app.features.simulation_migration.tools.effective_mode", lambda _: "off")
    llm = Responses(
        '```json\n{"text":"처음 뵙겠습니다"}\n```',
        json.dumps({"text": "가" * 301}),
        '{"message":"반갑습니다"}',
        '{"text":"오늘은 어땠어요","speaker":"a"}',
        '{"content":"편하게 쉬었어요"}',
        '{"text":"다음에 뵈어요"}',
        '{"text":"좋은 밤 되세요"}',
    )

    async def run():
        reports = []

        async def report(state):
            reports.append(state)
            return {"summary": "테스트"}

        result = await create_simulation_graph(llm=llm, write_report=report).ainvoke(
            {"persona_a": _persona(), "persona_b": _persona(), "nickname_a": "알파", "nickname_b": "베타", "turns": 3}
        )
        assert result.get("error") is None
        assert len(result["transcript"]) == 6 and len(reports) == 1
        assert len(llm.messages) == 7

    asyncio.run(run())
