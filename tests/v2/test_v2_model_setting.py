from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.features.practice import agents as v1_practice_agents
from app.v2 import llm as v2_llm
from app.v2.practice import agents as v2_practice_agents
from app.v2.simulation import llm as sim_llm


def test_default_llm_model_v2_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_MODEL_V2", raising=False)
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.llm_model_v2 == "google/gemini-3.1-flash-lite"
    finally:
        get_settings.cache_clear()


def test_llm_model_v2_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL_V2", "custom/test-v2-model")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.llm_model_v2 == "custom/test-v2-model"
    finally:
        get_settings.cache_clear()


def test_llm_model_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_MODEL_V2", raising=False)
    monkeypatch.setenv("LLM_MODEL", "custom/v1-model")
    # 과거 이슈 #116 변수가 설정되어도 영향 없음을 검증
    legacy_var = "".join(["LLM", "_", "MODEL", "3"])
    monkeypatch.setenv(legacy_var, "custom/bad-model")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.llm_model == "custom/v1-model"
        assert settings.llm_model_v2 == "google/gemini-3.1-flash-lite"
    finally:
        get_settings.cache_clear()


def test_simulation_call_text_model_arg(monkeypatch: pytest.MonkeyPatch) -> None:
    last_kwargs: dict = {}

    class FakeClient:
        def with_options(self, **kwargs):  # noqa: ANN003
            return self

        @property
        def chat(self):
            class Completions:
                async def create(self_inner, **kwargs):  # noqa: ANN003
                    last_kwargs.update(kwargs)
                    choice = SimpleNamespace(
                        message=SimpleNamespace(content="test response"),
                        finish_reason="stop",
                    )
                    return SimpleNamespace(
                        choices=[choice],
                        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10),
                    )

            return SimpleNamespace(completions=Completions())

    monkeypatch.setattr(sim_llm, "_get_client", lambda: FakeClient())

    async def _run() -> None:
        # model=None 일 때 기본값으로 llm_model_v2 가 넘어감
        await sim_llm.call_text(
            system="sys",
            user="user",
            max_tokens=50,
            timeout=5.0,
            name="test-call-text",
        )
        assert last_kwargs["model"] == get_settings().llm_model_v2

        # model 을 주면 그 값이 넘어감
        await sim_llm.call_text(
            system="sys",
            user="user",
            max_tokens=50,
            timeout=5.0,
            name="test-call-text",
            model="custom/explicit-model",
        )
        assert last_kwargs["model"] == "custom/explicit-model"

    asyncio.run(_run())


def test_simulation_call_tools_model_arg(monkeypatch: pytest.MonkeyPatch) -> None:
    last_kwargs: dict = {}

    class FakeClient:
        def with_options(self, **kwargs):  # noqa: ANN003
            return self

        @property
        def chat(self):
            class Completions:
                async def create(self_inner, **kwargs):  # noqa: ANN003
                    last_kwargs.update(kwargs)
                    choice = SimpleNamespace(
                        message=SimpleNamespace(
                            tool_calls=[
                                SimpleNamespace(
                                    function=SimpleNamespace(
                                        name="dummy_tool",
                                        arguments='{"key": "val"}',
                                    )
                                )
                            ],
                            content=None,
                        ),
                        finish_reason="stop",
                    )
                    return SimpleNamespace(
                        choices=[choice],
                        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10),
                    )

            return SimpleNamespace(completions=Completions())

    monkeypatch.setattr(sim_llm, "_get_client", lambda: FakeClient())
    monkeypatch.setattr(get_settings(), "simulation_v2_use_tools", True)

    dummy_tool = {
        "type": "function",
        "function": {
            "name": "dummy_tool",
            "description": "dummy",
            "parameters": {"type": "object", "properties": {"key": {"type": "string"}}},
        },
    }

    async def _run() -> None:
        # model=None 일 때 기본값으로 llm_model_v2 가 넘어감
        await sim_llm.call_tools(
            system="sys",
            user="user",
            tools=[dummy_tool],
            max_tokens=50,
            timeout=5.0,
            name="test-call-tools",
        )
        assert last_kwargs["model"] == get_settings().llm_model_v2

        # model 을 주면 그 값이 넘어감
        await sim_llm.call_tools(
            system="sys",
            user="user",
            tools=[dummy_tool],
            max_tokens=50,
            timeout=5.0,
            name="test-call-tools",
            model="custom/explicit-model",
        )
        assert last_kwargs["model"] == "custom/explicit-model"

    asyncio.run(_run())


def test_v2_llm_main_path_uses_llm_model_v2(monkeypatch: pytest.MonkeyPatch) -> None:
    last_kwargs: dict = {}

    class FakeClient:
        @property
        def chat(self):
            class Completions:
                async def create(self_inner, **kwargs):  # noqa: ANN003
                    last_kwargs.update(kwargs)
                    choice = SimpleNamespace(
                        message=SimpleNamespace(content="response text"),
                    )
                    return SimpleNamespace(choices=[choice])

            return SimpleNamespace(completions=Completions())

    monkeypatch.setattr(v2_llm, "_get_client", lambda: FakeClient())

    async def _run() -> None:
        await v2_llm._call(
            system="sys",
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=50,
            timeout=5.0,
            name="test-main-path",
            use_slm=False,
        )

    asyncio.run(_run())
    assert last_kwargs["model"] == get_settings().llm_model_v2


def test_v2_practice_streaming_uses_llm_model_v2(monkeypatch: pytest.MonkeyPatch) -> None:
    last_kwargs: dict = {}

    def _chunk(text: str | None) -> SimpleNamespace:
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])

    async def fake_create(**kwargs):  # noqa: ANN003
        last_kwargs.update(kwargs)

        async def stream():
            yield _chunk("안녕하세요")

        return stream()

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    monkeypatch.setattr(v2_practice_agents, "_get_client", lambda: client)

    async def _run() -> list[str]:
        partner = v2_practice_agents.PartnerAgent()
        return [c async for c in partner.reply(system="SYS", history=[{"role": "user", "content": "안녕"}])]

    chunks = asyncio.run(_run())
    assert chunks == ["안녕하세요"]
    assert last_kwargs["model"] == get_settings().llm_model_v2


def test_v1_practice_streaming_uses_llm_model(monkeypatch: pytest.MonkeyPatch) -> None:
    last_kwargs: dict = {}

    def _chunk(text: str | None) -> SimpleNamespace:
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])

    async def fake_create(**kwargs):  # noqa: ANN003
        last_kwargs.update(kwargs)

        async def stream():
            yield _chunk("v1 응답")

        return stream()

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    monkeypatch.setattr(v1_practice_agents, "_get_client", lambda: client)

    monkeypatch.setenv("LLM_MODEL", "custom/v1-model-name")
    get_settings.cache_clear()
    try:

        async def _run() -> list[str]:
            partner = v1_practice_agents.PartnerAgent()
            return [c async for c in partner.reply(system="SYS", history=[{"role": "user", "content": "안녕"}])]

        chunks = asyncio.run(_run())
        assert chunks == ["v1 응답"]
        assert last_kwargs["model"] == "custom/v1-model-name"
        assert get_settings().llm_model == "custom/v1-model-name"
        assert get_settings().llm_model_v2 == "google/gemini-3.1-flash-lite"
    finally:
        get_settings.cache_clear()
