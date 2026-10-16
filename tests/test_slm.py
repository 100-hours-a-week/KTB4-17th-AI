"""태깅·추출 SLM 라우팅 — 대상 호출만 SLM 으로, 실패하면 메인 LLM 으로, 내려가 있으면 곧장 메인으로."""

import asyncio
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.core import llm_retry, slm
from app.core.config import get_settings
from app.features.persona import agents


@pytest.fixture(autouse=True)
def _slm_env(monkeypatch):
    monkeypatch.setenv("SLM_BASE_URL", "https://pod-8000.proxy.runpod.test/v1")
    monkeypatch.setenv("SLM_TAGGING_MODEL", "tagging-v1")
    monkeypatch.setenv("SLM_EXTRACTION_MODEL", "kanana")
    monkeypatch.setenv("LLM_MODEL", "google/gemini-2.5-flash-lite")
    monkeypatch.setenv("SLM_PROBE_INTERVAL_S", "0.01")
    monkeypatch.setattr(llm_retry, "RETRY_BACKOFF_S", 0)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _fake_client(outcomes):
    """create() 가 불릴 때마다 outcomes 를 하나씩 꺼낸다. 예외면 raise, 문자열이면 그 답."""
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=outcome))])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), calls


def _wire(monkeypatch, slm_outcomes, main_outcomes, healthy=True):
    slm_client, slm_calls = _fake_client(slm_outcomes)
    main_client, main_calls = _fake_client(main_outcomes)
    monkeypatch.setattr(slm, "_get_client", lambda: slm_client)
    monkeypatch.setattr(agents, "_get_client", lambda: main_client)

    async def check():
        return healthy

    monkeypatch.setattr(slm, "health", slm.Health(check=check))
    return slm_calls, main_calls


def _connection_error():
    return openai.APIConnectionError(request=httpx.Request("POST", "https://pod-8000.proxy.runpod.test/v1"))


def _bad_gateway():
    request = httpx.Request("POST", "https://pod-8000.proxy.runpod.test/v1")
    return openai.InternalServerError("502", response=httpx.Response(502, request=request), body=None)


def _call(name):
    return agents._call(system="SYS", messages=[], max_tokens=10, timeout=1.0, name=name)


def test_tagging_goes_to_slm_with_its_model(monkeypatch):
    slm_calls, main_calls = _wire(monkeypatch, ["slm 답"], [])

    assert asyncio.run(_call("persona-tagging")) == "slm 답"
    assert slm_calls[0]["model"] == "tagging-v1"
    assert main_calls == []


def test_extraction_calls_use_extraction_model(monkeypatch):
    slm_calls, _ = _wire(monkeypatch, ["a", "b"], [])

    asyncio.run(_call("persona-extraction"))
    asyncio.run(_call("persona-extraction-style"))

    assert [c["model"] for c in slm_calls] == ["kanana", "kanana"]


def test_conversation_stays_on_main_llm(monkeypatch):
    slm_calls, main_calls = _wire(monkeypatch, [], ["gemini 답"])

    assert asyncio.run(_call("persona-conversation")) == "gemini 답"
    assert slm_calls == []
    assert main_calls[0]["model"] == "google/gemini-2.5-flash-lite"


def test_task_without_model_stays_on_main_llm(monkeypatch):
    monkeypatch.setenv("SLM_EXTRACTION_MODEL", "")
    get_settings.cache_clear()
    slm_calls, main_calls = _wire(monkeypatch, [], ["gemini 답"])

    assert asyncio.run(_call("persona-extraction")) == "gemini 답"
    assert slm_calls == []


def test_without_slm_base_url_nothing_changes(monkeypatch):
    monkeypatch.setenv("SLM_BASE_URL", "")
    get_settings.cache_clear()
    slm_calls, main_calls = _wire(monkeypatch, [], ["gemini 답"])

    assert asyncio.run(_call("persona-tagging")) == "gemini 답"
    assert slm_calls == []


@pytest.mark.parametrize("error", [_connection_error(), _bad_gateway(), TimeoutError()], ids=["conn", "502", "timeout"])
def test_cold_start_falls_back_once_then_skips_slm(monkeypatch, error):
    """Pod 가 아직 모델을 올리는 중 — 첫 요청만 SLM 실패를 겪고, 다음 요청은 SLM 을 건너뛴다."""
    slm_calls, main_calls = _wire(monkeypatch, [error], ["gemini 1", "gemini 2"], healthy=False)

    async def scenario():
        first = await _call("persona-tagging")
        second = await _call("persona-tagging")
        await slm.health.stop()
        return first, second

    assert asyncio.run(scenario()) == ("gemini 1", "gemini 2")
    assert len(slm_calls) == 1  # 두 번째는 SLM 에 보내지 않았다
    assert len(main_calls) == 2
    assert slm.health.up is False


def test_slm_is_used_again_after_health_recovers(monkeypatch):
    slm_calls, main_calls = _wire(monkeypatch, [_connection_error(), "slm 복구"], ["gemini"], healthy=True)

    async def scenario():
        await _call("persona-tagging")  # 실패 → 내려감 → 백그라운드 확인 시작
        for _ in range(100):
            if slm.health.up:
                break
            await asyncio.sleep(0.01)
        return await _call("persona-tagging")

    assert asyncio.run(scenario()) == "slm 복구"
    assert len(slm_calls) == 2
    assert len(main_calls) == 1


def test_slm_request_error_falls_back_without_marking_down(monkeypatch):
    """400 같은 요청 오류는 서버가 살아 있다는 뜻 — 이번 요청만 폴백하고 다음에도 SLM 을 쓴다."""
    request = httpx.Request("POST", "https://pod-8000.proxy.runpod.test/v1")
    bad_request = openai.BadRequestError("400", response=httpx.Response(400, request=request), body=None)
    slm_calls, main_calls = _wire(monkeypatch, [bad_request], ["gemini"])

    assert asyncio.run(_call("persona-tagging")) == "gemini"
    assert slm.health.up is True


def test_slm_is_not_retried_but_main_keeps_its_retry(monkeypatch):
    slm_calls, main_calls = _wire(monkeypatch, [TimeoutError()], [TimeoutError(), "gemini 재시도 성공"], healthy=False)

    async def scenario():
        result = await _call("persona-tagging")
        await slm.health.stop()
        return result

    assert asyncio.run(scenario()) == "gemini 재시도 성공"
    assert len(slm_calls) == 1
    assert len(main_calls) == 2


def test_broken_json_from_slm_is_retried_on_main_llm(monkeypatch):
    slm_calls, main_calls = _wire(monkeypatch, ["태그는 contact_rhythm 입니다"], ['{"primary": ["contact_rhythm"]}'])

    data = asyncio.run(agents._call_json(system="SYS", messages=[], max_tokens=10, timeout=1.0, name="persona-tagging"))

    assert data == {"primary": ["contact_rhythm"]}
    assert len(main_calls) == 1
    assert slm.health.up is True  # 서버는 살아 있다


def test_tagging_agent_end_to_end_through_slm(monkeypatch):
    _wire(monkeypatch, ['{"primary": ["contact_rhythm"], "secondary": [], "off_topic": false}'], [])

    tags = asyncio.run(agents.TaggingAgent().tag("연락은 자주 하는 편이세요?", "하루에 한두 번이면 충분해요"))

    assert tags is not None
    assert tags.primary == ["contact_rhythm"]


def test_startup_check_marks_down_while_model_is_loading(monkeypatch):
    _wire(monkeypatch, [], [], healthy=False)

    async def scenario():
        await slm.start()
        await asyncio.sleep(0.02)
        up = slm.health.up
        await slm.stop()
        return up

    assert asyncio.run(scenario()) is False


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("https://pod-8000.proxy.runpod.net/v1", "https://pod-8000.proxy.runpod.net/health"),
        ("https://pod-8000.proxy.runpod.net/v1/", "https://pod-8000.proxy.runpod.net/health"),
        ("http://localhost:8000", "http://localhost:8000/health"),
    ],
)
def test_health_url_drops_v1(base, expected):
    assert slm.health_url(base) == expected
