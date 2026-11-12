"""Actual model calls and accepted results must tell the same story in telemetry."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.core import llm_retry, observability, slm
from app.core.config import get_settings
from app.features.persona import agents as v1
from app.v2 import llm as v2


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setenv("SLM_BASE_URL", "https://slm.test/v1")
    monkeypatch.setenv("SLM_TAGGING_MODEL", "configured-tagging")
    monkeypatch.setenv("SLM_EXTRACTION_MODEL", "configured-extraction")
    monkeypatch.setenv("LLM_MODEL", "configured-main-v1")
    monkeypatch.setenv("LLM_MODEL_V2", "configured-main-v2")
    monkeypatch.setattr(llm_retry, "RETRY_BACKOFF_S", 0)
    monkeypatch.setattr(slm, "health", slm.Health())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def events(monkeypatch):
    rows = []

    class Telemetry:
        def create_event(self, **kwargs):
            rows.append(deepcopy(kwargs))

        def update_current_span(self, **kwargs):
            pass

    monkeypatch.setattr(observability, "get_client", lambda: Telemetry(), raising=False)
    return rows


def client(outcomes, calls):
    async def create(**kwargs):
        calls.append(kwargs)
        item = outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        text, model = item
        return SimpleNamespace(model=model, choices=[SimpleNamespace(message=SimpleNamespace(content=text))])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def wire(monkeypatch, module, small=(), main=()):
    small_calls, main_calls = [], []
    small_client = client(list(small), small_calls)
    main_client = client(list(main), main_calls)
    monkeypatch.setattr(slm, "_get_client", lambda: small_client)
    monkeypatch.setattr(module, "_get_client", lambda: main_client)
    return small_calls, main_calls


def call(module, **kwargs):
    return module._call_json(system="system", messages=[], max_tokens=10, timeout=1, name="persona-tagging", **kwargs)


def result(events):
    return [e["metadata"] for e in events if e["name"] == "model-result"][-1]


@pytest.mark.parametrize("module", [v1, v2], ids=["v1", "v2"])
def test_slm_response_model_is_adopted_after_valid_json(monkeypatch, events, module):
    small, main = wire(monkeypatch, module, small=[('{"primary": []}', "served-small-revision")])
    assert asyncio.run(call(module)) == {"primary": []}
    info = result(events)
    assert info["slm_attempted"] is True
    assert info["slm_skip_reason"] is None
    assert info["requested_model"] == "configured-tagging"
    assert info["response_model"] == "served-small-revision"
    assert info["final_model"] == "served-small-revision"
    assert info["final_source"] == "slm"
    assert len(small) == 1 and main == []


@pytest.mark.parametrize("module,model", [(v1, "configured-main-v1"), (v2, "configured-main-v2")])
@pytest.mark.parametrize(
    "case,reason",
    [
        ("address", "slm_base_url_not_configured"),
        ("task", "task_not_supported"),
        ("model", "slm_model_not_configured"),
        ("health", "slm_unhealthy"),
        ("disabled", "slm_disabled"),
    ],
)
def test_skip_reason_without_slm_attempt(monkeypatch, events, module, model, case, reason):
    kwargs = dict(system="s", messages=[], max_tokens=10, timeout=1, name="persona-tagging")
    if case == "address":
        monkeypatch.setenv("SLM_BASE_URL", "")
    elif case == "task":
        kwargs["name"] = "v2-persona-extraction"
    elif case == "model":
        monkeypatch.setenv("SLM_TAGGING_MODEL", "")
    elif case == "health":
        slm.health.up = False
    elif case == "disabled":
        kwargs["use_slm"] = False
    get_settings.cache_clear()
    small, main = wire(monkeypatch, module, main=[("{}", "served-main")])
    assert asyncio.run(module._call_json(**kwargs)) == {}
    info = result(events)
    assert info["slm_attempted"] is False
    assert info["slm_skip_reason"] == reason
    assert info["fallback_reason"] is None
    assert info["requested_model"] == model
    assert info["final_model"] == "served-main"
    assert info["final_source"] == "main_llm"
    assert small == [] and len(main) == 1


def server_error():
    req = httpx.Request("POST", "https://slm.test/v1")
    return openai.InternalServerError("server error", response=httpx.Response(503, request=req), body=None)


@pytest.mark.parametrize("module", [v1, v2])
@pytest.mark.parametrize(
    "outcome,reason",
    [
        (openai.APIConnectionError(request=httpx.Request("POST", "https://slm.test")), "connection_error"),
        (TimeoutError(), "timeout"),
        (server_error(), "server_error"),
        (("", "served-small"), "empty_response"),
        (("broken json", "served-small"), "invalid_json"),
    ],
)
def test_slm_failure_records_reason_and_adopts_main(monkeypatch, events, module, outcome, reason):
    small, main = wire(monkeypatch, module, small=[outcome], main=[("{}", "served-main")])

    async def scenario():
        try:
            return await call(module)
        finally:
            await slm.health.stop()

    assert asyncio.run(scenario()) == {}
    info = result(events)
    assert info["slm_attempted"] is True
    assert info["slm_skip_reason"] is None
    assert info["fallback_reason"] == reason
    assert info["final_model"] == "served-main"
    assert info["final_source"] == "main_llm"
    assert len(small) == len(main) == 1
    attempts = [e["metadata"] for e in events if e["name"] == "model-call-attempt"]
    assert [e["requested_model"] for e in attempts] == [
        "configured-tagging",
        "configured-main-v1" if module is v1 else "configured-main-v2",
    ]
    assert len({e["model_operation_id"] for e in attempts + [info]}) == 1


@pytest.mark.parametrize("module", [v1, v2])
def test_main_invalid_json_after_empty_slm_is_final_failure_without_extra_call(monkeypatch, events, module):
    small, main = wire(monkeypatch, module, small=[("", "small")], main=[("bad json", "main")])
    with pytest.raises(module.LLMError, match="invalid JSON"):
        asyncio.run(call(module))
    assert len(small) == len(main) == 1
    info = result(events)
    assert info["final_source"] == "failed"
    assert info["final_model"] is None
    assert info["response_model"] == "main"
    assert info["failure_reason"] == "invalid_json"


@pytest.mark.parametrize("module", [v1, v2])
def test_response_without_model_uses_requested_identifier(monkeypatch, events, module):
    wire(monkeypatch, module, small=[("{}", None)])
    assert asyncio.run(call(module)) == {}
    assert result(events)["response_model"] is None
    assert result(events)["final_model"] == "configured-tagging"


@pytest.mark.parametrize("module", [v1, v2])
def test_telemetry_failure_does_not_break_actual_call(monkeypatch, module):
    def broken():
        raise RuntimeError("telemetry unavailable")

    monkeypatch.setattr(observability, "get_client", broken, raising=False)
    wire(monkeypatch, module, small=[("{}", "small")])
    assert asyncio.run(call(module)) == {}


@pytest.mark.parametrize("strict", [False, True])
def test_tagging_none_is_failed_until_a_consumer_actually_adopts_rules(monkeypatch, events, strict):
    wire(monkeypatch, v1, small=[("not json", "small")], main=[("still not json", "main")])
    assert asyncio.run(v1.TaggingAgent().tag("question", "answer", strict=strict)) is None
    info = result(events)
    assert info["final_source"] == "failed"
    assert info["final_model"] is None
    assert info["response_model"] == "main"
    assert info["failure_reason"] == "invalid_json"


def test_tagging_schema_rejection_is_not_an_adopted_slm_result(monkeypatch, events):
    wire(monkeypatch, v1, small=[('{"primary": ["invented"]}', "small")])
    assert asyncio.run(v1.TaggingAgent().tag("q", "a", strict=True)) is None
    assert result(events)["final_source"] == "failed"
    assert result(events)["failure_reason"] == "schema_validation_failed"
    assert result(events)["final_model"] is None


def test_v1_extraction_schema_rejection_is_final_failure(monkeypatch, events):
    wire(monkeypatch, v1, small=[('{"avoidance": 101}', "small")])
    with pytest.raises(v1.BuildFailed):
        asyncio.run(v1.ExtractionAgent().extract([]))
    assert result(events)["final_source"] == "failed"
    assert result(events)["failure_reason"] == "schema_validation_failed"
    assert result(events)["final_model"] is None


@pytest.mark.parametrize("valid", [False, True])
def test_v2_extraction_finalizes_after_schema_validation(monkeypatch, events, valid):
    from app.v2.persona_extraction.agents import ConversationAgent, ExtractionFailed

    wire(monkeypatch, v2, main=[("{}" if valid else '{"style": "invalid"}', "main-v2")])
    if valid:
        asyncio.run(ConversationAgent().analyze("나[1]: synthetic", previous=None))
    else:
        with pytest.raises(ExtractionFailed):
            asyncio.run(ConversationAgent().analyze("나[1]: synthetic", previous=None))
    info = result(events)
    assert info["slm_attempted"] is False
    assert info["slm_skip_reason"] == "task_not_supported"
    assert info["final_source"] == ("main_llm" if valid else "failed")
    assert info["final_model"] == ("main-v2" if valid else None)


def test_persona_build_uses_rules_after_schema_rejection(monkeypatch, events):
    from test_persona_build_fallback import _build

    wire(monkeypatch, v1, small=[('{"avoidance": 101}', "small")])
    (draft,), _ = _build(agent=v1.ExtractionAgent())
    assert draft.source == "fallback"
    info = result(events)
    assert info["response_model"] == "small"
    assert info["final_model"] is None
    assert info["final_source"] == "rule_based"
    assert info["fallback_reason"] == "schema_validation_failed"


def test_task_context_does_not_share_parent_model_state(monkeypatch, events):
    wire(monkeypatch, v1, small=[("{}", "small-a"), ("{}", "small-b")])

    @observability.track_model_selection("persona-tagging")
    async def parent():
        return await asyncio.gather(call(v1), call(v1))

    assert asyncio.run(parent()) == [{}, {}]
    children = [e["metadata"] for e in events if e["name"] == "model-result" and e["metadata"]["final_model"]]
    assert {c["final_model"] for c in children} == {"small-a", "small-b"}
    assert len({c["model_operation_id"] for c in children}) == 2
    assert result(events)["final_model"] is None


def test_broken_propagation_does_not_break_application_result_or_swallow_error(monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("telemetry broken")

    monkeypatch.setattr(observability, "propagate_attributes", broken)
    metadata = observability.build_langfuse_metadata(feature="persona", operation="tagging")
    with observability.propagate_langfuse_metadata(metadata):
        value = "accepted"
    assert value == "accepted"
    with pytest.raises(ValueError, match="application error"), observability.propagate_langfuse_metadata(metadata):
        raise ValueError("application error")


def test_telemetry_failure_does_not_break_tagging_rule_fallback(monkeypatch):
    def broken():
        raise RuntimeError("telemetry unavailable")

    monkeypatch.setattr(observability, "get_client", broken)
    monkeypatch.setattr(v1, "get_client", broken)
    wire(monkeypatch, v1, small=[("bad json", "small")], main=[("bad json", "main")])
    assert asyncio.run(v1.TaggingAgent().tag("q", "a")) is None


@pytest.mark.parametrize("module", [v1, v2])
def test_main_model_retries_preserve_attempts_and_model_identity(monkeypatch, events, module):
    _, main = wire(monkeypatch, module, main=[TimeoutError(), ("{}", "actual-main")])
    assert asyncio.run(call(module, use_slm=False)) == {}
    attempts = [e["metadata"] for e in events if e["name"] == "model-call-attempt"]
    assert [a["attempt"] for a in attempts] == [1, 2]
    assert result(events)["final_model"] == "actual-main"
    assert len(main) == 2


@pytest.mark.parametrize("module", [v1, v2])
def test_invalid_json_uses_actual_slm_response_even_if_health_changed(monkeypatch, events, module):
    _, main = wire(monkeypatch, module, main=[("{}", "actual-main")])

    async def create(**kwargs):
        slm.health.up = False
        return SimpleNamespace(model="small", choices=[SimpleNamespace(message=SimpleNamespace(content="bad"))])

    small_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(slm, "_get_client", lambda: small_client)
    assert asyncio.run(call(module)) == {}
    assert len(main) == 1
    assert result(events)["slm_attempted"] is True
    assert result(events)["slm_skip_reason"] is None
    assert result(events)["fallback_reason"] == "invalid_json"


@pytest.mark.parametrize("kind", ["extract", "reconcile", "style"])
def test_other_schema_validators_do_not_adopt_rejected_model(monkeypatch, events, kind):
    from app.features.persona_extraction.agents import StyleAgent
    from app.v2.persona.agents import PersonaMemoryExtractor, PersonaMemoryReconcileJudge

    if kind == "style":
        wire(monkeypatch, v1, main=[('{"style": "invalid"}', "main")])
        operation = StyleAgent().analyze(["synthetic"], previous=None)
    else:
        wire(monkeypatch, v2, main=[('{"facts": "invalid", "decisions": "invalid"}', "main")])
        operation = (
            PersonaMemoryExtractor().extract(pairs=[], current_date="2026-11-02", current_core={})
            if kind == "extract"
            else PersonaMemoryReconcileJudge().judge_batch([])
        )
    with pytest.raises(Exception, match="invalid"):
        asyncio.run(operation)
    assert result(events)["final_model"] is None
    assert result(events)["final_source"] == "failed"
    assert result(events)["failure_reason"] == "schema_validation_failed"


def test_workflow_span_replaces_nullable_provenance_using_actual_sdk_serialization(monkeypatch):
    import json

    from langfuse._client.attributes import create_span_attributes

    attributes = {}

    class Telemetry:
        def update_current_span(self, **kwargs):
            attributes.update(create_span_attributes(**kwargs))

        def create_event(self, **kwargs):
            pass

    monkeypatch.setattr(observability, "get_client", lambda: Telemetry())
    wire(monkeypatch, v1, small=[("", "empty-small")], main=[RuntimeError("no response")])
    with pytest.raises(v1.LLMError):
        asyncio.run(call(v1))
    info = json.loads(attributes["langfuse.observation.metadata.model_selection"])
    assert info["requested_model"] == "configured-main-v1"
    assert info["response_model"] is None
    assert info["final_model"] is None
    assert info["final_source"] == "failed"


@pytest.mark.parametrize("mixed", [False, True])
def test_reconcile_rules_mark_complete_or_partial_replacement(monkeypatch, events, mixed):
    from app.v2.persona.agents import PersonaMemoryReconcileJudge

    payload = '{"decisions": [{"new_fact_index": 0, "action": "NOOP"}]}' if mixed else '{"decisions": []}'
    wire(monkeypatch, v2, main=[(payload, "actual-main")])
    items = [
        (0, {"fact": "synthetic-a", "category": "취미"}, [], []),
        (1, {"fact": "synthetic-b", "category": "취미"}, [], []),
    ]
    decisions = asyncio.run(PersonaMemoryReconcileJudge().judge_batch(items))
    assert decisions[1]["action"] == "ADD"
    assert result(events)["final_source"] == ("mixed" if mixed else "rule_based")
    assert result(events)["final_model"] == ("actual-main" if mixed else None)
    assert result(events)["rule_reason"] == "invalid_reconcile_decision"


def test_first_turn_template_repair_is_mixed_source(monkeypatch, events):
    import json

    from test_persona_agents import _topic, _valid_parts

    wire(monkeypatch, v1, main=[(json.dumps(_valid_parts(answer_prompt="대답해 주실래요?")), "main")])
    utterance = asyncio.run(
        v1.ConversationAgent().generate(history=[], topic=_topic(), turn_index=0, total_turns=5, nickname="민수")
    )
    assert "대답해 주실래요?" not in utterance.text
    assert result(events)["final_source"] == "mixed"
    assert result(events)["final_model"] == "main"
    assert result(events)["rule_reason"] == "first_turn_segments_repaired"


@pytest.mark.parametrize("timeout", [False, True])
def test_tagging_consumer_records_rules_even_if_background_model_later_succeeds(monkeypatch, events, timeout):
    from conftest import with_db
    from test_persona_answer import FakeConversation

    from app.features.persona.repository import PersonaRepository
    from app.features.persona.service import OnboardingService

    small_calls = []

    async def create(**kwargs):
        small_calls.append(kwargs)
        text = "" if timeout else "not json"
        return SimpleNamespace(
            model="background-small", choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
        )

    small_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    wire(monkeypatch, v1, main=[("also invalid", "main")])
    if timeout:
        main_calls = []

        async def main_create(**kwargs):
            main_calls.append(kwargs)
            if len(main_calls) == 1:
                await asyncio.sleep(0.04)
            text = '{"primary": ["routine"], "secondary": [], "off_topic": false}'
            return SimpleNamespace(
                model="background-main", choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
            )

        main_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=main_create)))
        monkeypatch.setattr(v1, "_get_client", lambda: main_client)
    monkeypatch.setattr(slm, "_get_client", lambda: small_client)
    monkeypatch.setattr(get_settings(), "onboarding_tag_timeout_s", 0.01 if timeout else 1)

    async def scenario(factory):
        async with factory() as db:
            svc = OnboardingService(PersonaRepository(db))
            svc.conversation = FakeConversation()
            first = await svc.start("synthetic", "model-provenance-test")
            session = await svc.repo.get_session(first.session_id)
            await svc.submit_answer(session, "synthetic answer")
            await asyncio.sleep(0.08)
            return session.coverage

    coverage = asyncio.run(with_db(scenario))
    assert coverage["primary"]
    adopted = [e["metadata"] for e in events if e["name"] == "model-consumer-result"]
    assert len(adopted) == 1
    assert adopted[0]["final_source"] == "rule_based"
    assert adopted[0]["final_model"] is None
    assert adopted[0]["rule_reason"] == ("tagging_wait_timeout" if timeout else "tagging_failed")
    model_result = result(events)
    assert model_result["final_source"] == ("main_llm" if timeout else "failed")
    assert adopted[0]["model_operation_id"] == model_result["model_operation_id"]


def test_extraction_guardrail_removal_is_partial_rule_result(monkeypatch, events):
    import json

    monkeypatch.setenv("GUARDRAIL_MODE", "enforce")
    get_settings.cache_clear()
    payload = {"avoidance": 70, "narrative": {"headline": "저는 AI예요.", "body": "제가 챗봇이에요.", "traits": []}}
    wire(monkeypatch, v1, small=[(json.dumps(payload), "small")])
    extracted = asyncio.run(v1.ExtractionAgent().extract([], user_key="synthetic"))
    assert extracted.narrative is None and extracted.avoidance == 70
    assert result(events)["final_source"] == "mixed"
    assert result(events)["rule_reason"] == "extraction_guardrail_rejected"


def test_persona_build_marks_rules_that_drop_ungrounded_model_scores(monkeypatch, events):
    from test_persona_build_fallback import _build

    wire(monkeypatch, v1, small=[('{"avoidance": 70, "anxiety": 90}', "small")])
    (draft,), _ = _build(agent=v1.ExtractionAgent())
    assert draft.scores["avoidance"] == 70 and draft.scores["anxiety"] is None
    assert result(events)["final_source"] == "mixed"
    assert result(events)["final_model"] == "small"
    assert result(events)["rule_reason"] == "persona_result_filtered"
