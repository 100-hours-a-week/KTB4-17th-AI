"""LLM 호출 재시도 — 다시 보내면 될 실패만, 정해진 횟수만큼, 스트림은 첫 조각 전까지만."""

import asyncio
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.core import llm_retry
from app.features.persona import agents as persona_agents
from app.features.practice import agents as practice_agents


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(llm_retry, "RETRY_BACKOFF_S", 0)


def _client(monkeypatch, module, outcomes):
    """create() 가 호출될 때마다 outcomes 를 하나씩 꺼낸다. 예외면 raise, 아니면 그대로 반환."""
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(module, "_get_client", lambda: client)
    return calls


def _completion(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def _persona_call():
    return asyncio.run(persona_agents._call(system="SYS", messages=[], max_tokens=10, timeout=1.0))


def _rate_limited():
    request = httpx.Request("POST", "https://example.test")
    response = httpx.Response(429, request=request)
    return openai.RateLimitError("rate limited", response=response, body=None)


# ── persona: 단발 호출 ────────────────────────────────


def test_persona_retries_once_after_timeout(monkeypatch):
    calls = _client(monkeypatch, persona_agents, [TimeoutError(), _completion("두 번째에 성공")])

    assert _persona_call() == "두 번째에 성공"
    assert len(calls) == 2


def test_persona_retries_rate_limit_and_empty_response(monkeypatch):
    calls = _client(monkeypatch, persona_agents, [_rate_limited(), _completion("")])

    with pytest.raises(persona_agents.LLMError, match="empty response"):
        _persona_call()
    assert len(calls) == 2  # 재시도 1회까지만


def test_persona_gives_up_after_configured_attempts(monkeypatch):
    calls = _client(monkeypatch, persona_agents, [TimeoutError(), TimeoutError(), TimeoutError()])

    with pytest.raises(persona_agents.LLMError, match="timeout after 1.0s"):
        _persona_call()
    assert len(calls) == 2


def test_persona_does_not_retry_non_transient_error(monkeypatch):
    calls = _client(monkeypatch, persona_agents, [RuntimeError("401 invalid api key"), _completion("안 감")])

    with pytest.raises(persona_agents.LLMError, match="401"):
        _persona_call()
    assert len(calls) == 1


def test_retry_can_be_disabled(monkeypatch):
    monkeypatch.setattr(llm_retry, "attempts", lambda: 1)
    calls = _client(monkeypatch, persona_agents, [TimeoutError(), _completion("안 감")])

    with pytest.raises(persona_agents.LLMError):
        _persona_call()
    assert len(calls) == 1


# ── practice: 스트리밍 ────────────────────────────────


def _stream(*chunks, error=None):
    async def gen():
        for text in chunks:
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])
        if error:
            raise error

    return gen()


def _practice_reply():
    async def collect():
        return [x async for x in practice_agents._stream(system="SYS", messages=[], max_tokens=10, timeout=1.0)]

    return asyncio.run(collect())


def test_practice_retries_when_nothing_was_sent(monkeypatch):
    calls = _client(monkeypatch, practice_agents, [TimeoutError(), _stream("안녕", "하세요")])

    assert _practice_reply() == ["안녕", "하세요"]
    assert len(calls) == 2


def test_practice_does_not_retry_after_first_chunk(monkeypatch):
    # 이미 보낸 "안녕" 뒤에 다시 보내면 답변이 겹친다 — 그대로 실패시킨다
    calls = _client(monkeypatch, practice_agents, [_stream("안녕", error=TimeoutError()), _stream("다시")])

    with pytest.raises(practice_agents.LLMError, match="timeout"):
        _practice_reply()
    assert len(calls) == 1
