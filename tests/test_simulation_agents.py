import asyncio
import json

import pytest

from app.features.persona.schemas import PersonaResponse
from app.features.simulation import agents
from app.features.simulation.agents import LLMError, SimulationAgent, SimulationFailed
from app.features.simulation.schemas import ReportNarrative


def _llm_returns(monkeypatch, text):
    """_call(LLM 호출 경계)만 가짜로 — 그 뒤의 JSON 추출·스키마 검증은 진짜 코드가 돈다."""
    seen = {}

    async def fake_call(*, system, messages, max_tokens, timeout, name="simulation-llm-call", metadata=None):
        seen.update(
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            timeout=timeout,
            name=name,
            metadata=metadata,
        )
        if isinstance(text, Exception):
            raise text
        return text

    monkeypatch.setattr(agents, "_call", fake_call)
    return seen


def _parse(monkeypatch, text):
    _llm_returns(monkeypatch, text)
    return asyncio.run(agents._call_json(system="", messages=[], max_tokens=1, timeout=1))


@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```JSON\n{"a": 1}\n```',
        '결과입니다:\n```json\n{"a": 1}\n```',
        '다음과 같아요.\n{"a": 1}\n이상입니다.',
    ],
)
def test_json_is_extracted_from_common_llm_wrappings(monkeypatch, raw):
    assert _parse(monkeypatch, raw) == {"a": 1}


def test_empty_or_non_json_reply_is_llm_error(monkeypatch):
    with pytest.raises(LLMError) as exc:
        _parse(monkeypatch, "죄송해요, 지금은 답할 수 없어요.")
    assert exc.value.reason == "invalid_json"


PERSONA = PersonaResponse(persona_id="p", scores={})
NARRATIVE = {"headline": "연락 리듬이 맞는 두 사람", "summary": "잘 맞아요."}


def _run_simulation(turns=3, name_a="민수", name_b="지수"):
    return asyncio.run(
        SimulationAgent().run(
            persona_a=PERSONA,
            persona_b=PERSONA,
            name_a=name_a,
            name_b=name_b,
            turns=turns,
            area_scores={},
            dim_scores={},
        )
    )


def test_simulation_parses_script_and_report(monkeypatch):
    payload = {
        "transcript": [{"speaker": "a", "text": "안녕하세요"}, {"speaker": "b", "text": "반가워요"}],
        "report": {**NARRATIVE, "unknown_key": "무시"},
        "extra": "무시",
    }
    _llm_returns(monkeypatch, "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```")

    out = _run_simulation()

    assert [(line.speaker, line.text) for line in out.transcript] == [("a", "안녕하세요"), ("b", "반가워요")]
    assert out.report.headline == "연락 리듬이 맞는 두 사람"


def test_simulation_script_with_null_ideal_fit_value_does_not_fail(monkeypatch):
    """실제로 503(invalid_script)까지 냈던 사례 — report.ideal_fit 안의 null 값 하나로 대본 전체가 버려졌다."""
    payload = {
        "transcript": [{"speaker": "a", "text": "안녕하세요"}, {"speaker": "b", "text": "반가워요"}],
        "report": {**NARRATIVE, "ideal_fit": {"ideal_warmth": 80, "ideal_status": None}},
    }
    _llm_returns(monkeypatch, json.dumps(payload, ensure_ascii=False))

    out = _run_simulation()

    assert out.report.ideal_fit == {"ideal_warmth": 80, "ideal_status": None}


def test_simulation_script_with_real_names_instead_of_ab_is_normalized(monkeypatch):
    """실제로 503(Literal["a","b"] 검증 실패)까지 냈던 사례 — LLM 이 speaker 에 a/b 대신 실제 닉네임을 준다.

    name_a="민수", name_b="지수" 는 _run_simulation() 의 기본값과 맞춘 것."""
    payload = {
        "transcript": [
            {"speaker": "민수", "text": "안녕하세요"},
            {"speaker": "지수", "text": "반가워요"},
            {"speaker": "민수", "text": "주말에 뭐 하세요?"},
            {"speaker": "지수", "text": "러닝해요"},
        ],
        "report": NARRATIVE,
    }
    _llm_returns(monkeypatch, json.dumps(payload, ensure_ascii=False))

    out = _run_simulation()

    assert [(line.speaker, line.text) for line in out.transcript] == [
        ("a", "안녕하세요"),
        ("b", "반가워요"),
        ("a", "주말에 뭐 하세요?"),
        ("b", "러닝해요"),
    ]


def test_simulation_script_drops_lines_with_unrecognized_speaker():
    data = {
        "transcript": [
            {"speaker": "민수", "text": "안녕하세요"},
            {"speaker": "사회자", "text": "이제 소개팅을 시작하겠습니다"},  # 못 알아보는 화자 — 버려짐
            {"speaker": "지수", "text": "반가워요"},
        ]
    }

    out = agents._normalize_speaker_labels(data, "민수", "지수")

    assert [(line["speaker"], line["text"]) for line in out["transcript"]] == [
        ("a", "안녕하세요"),
        ("b", "반가워요"),
    ]


def test_simulation_asks_for_requested_turns_with_token_budget(monkeypatch):
    seen = _llm_returns(monkeypatch, LLMError("stop here"))

    with pytest.raises(SimulationFailed):
        _run_simulation(turns=5)

    user = seen["messages"][0]["content"]
    assert "턴 수: 5 왕복" in user
    assert "총 10줄" in user
    assert seen["max_tokens"] == 3000 + 300 * 5
    assert seen["name"] == "simulation-run"


def test_simulation_timeout_comes_from_settings(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("SIMULATION_SCRIPT_TIMEOUT_S", "7.5")
    get_settings.cache_clear()
    try:
        seen = _llm_returns(monkeypatch, LLMError("stop here"))
        with pytest.raises(SimulationFailed):
            _run_simulation()
        assert seen["timeout"] == 7.5
    finally:
        get_settings.cache_clear()


def test_llm_error_reason_propagates_to_simulation_failed(monkeypatch):
    _llm_returns(monkeypatch, LLMError("stop here", reason="timeout"))

    with pytest.raises(SimulationFailed) as exc:
        _run_simulation()

    assert exc.value.reason == "timeout"


def test_invalid_script_shape_has_invalid_script_reason(monkeypatch):
    payload = json.dumps({"transcript": [{"speaker": "a", "text": "안녕"}], "report": NARRATIVE})
    _llm_returns(monkeypatch, payload)

    with pytest.raises(SimulationFailed) as exc:
        _run_simulation()

    assert exc.value.reason == "invalid_script"


def test_call_limits_concurrency_to_simulation_max_inflight(monkeypatch):
    """settings.simulation_max_inflight=1 이면 두 _call() 이 겹치지 않는다 (한 번에 하나씩)."""
    from types import SimpleNamespace

    from app.core.config import get_settings

    active = 0
    max_active = 0

    class FakeCompletions:
        async def create(self, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.05)
            active -= 1
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(agents, "_get_client", lambda: fake_client)
    monkeypatch.setenv("SIMULATION_MAX_INFLIGHT", "1")
    get_settings.cache_clear()
    agents._semaphore.cache_clear()

    async def both():
        await asyncio.gather(
            agents._call(system="s", messages=[], max_tokens=10, timeout=5),
            agents._call(system="s", messages=[], max_tokens=10, timeout=5),
        )

    try:
        asyncio.run(both())
        assert max_active == 1
    finally:
        get_settings.cache_clear()
        agents._semaphore.cache_clear()


def _fake_client(monkeypatch, *, content="{}", finish_reason="stop", error=None, seen=None):
    from types import SimpleNamespace

    class FakeCompletions:
        async def create(self, **kwargs):
            if seen is not None:
                seen.update(kwargs)
            if error is not None:
                raise error
            msg = SimpleNamespace(content=content)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=finish_reason)], usage=None)

    monkeypatch.setattr(
        agents, "_get_client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    )


def test_call_truncated_output_is_truncated_reason(monkeypatch):
    """max_tokens 에서 잘린 응답 — 실제로 invalid_json 503 으로 보였던 사례. 원인을 구분해서 올린다."""
    _fake_client(monkeypatch, content='{"transcript": [{"speaker": "a", "tex', finish_reason="length")

    with pytest.raises(LLMError) as exc:
        asyncio.run(agents._call(system="s", messages=[], max_tokens=10, timeout=5))

    assert exc.value.reason == "truncated"


def test_call_upstream_status_error_is_upstream_error(monkeypatch):
    import httpx
    from openai import APIStatusError

    req = httpx.Request("POST", "https://llm.test/v1/chat/completions")
    err = APIStatusError("bad gateway", response=httpx.Response(502, request=req), body=None)
    _fake_client(monkeypatch, error=err)

    with pytest.raises(LLMError) as exc:
        asyncio.run(agents._call(system="s", messages=[], max_tokens=10, timeout=5))

    assert exc.value.reason == "upstream_error"
    assert "502" in str(exc.value)


def test_call_requests_json_mode_by_default(monkeypatch):
    seen = {}
    _fake_client(monkeypatch, content='{"a": 1}', seen=seen)

    asyncio.run(agents._call(system="s", messages=[], max_tokens=10, timeout=5))

    assert seen["response_format"] == {"type": "json_object"}


def _llm_returns_sequence(monkeypatch, replies):
    calls = []

    async def fake_call(*, system, messages, max_tokens, timeout, name="simulation-llm-call", metadata=None):
        calls.append({"max_tokens": max_tokens, "timeout": timeout})
        reply = replies[len(calls) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(agents, "_call", fake_call)
    return calls


GOOD_SCRIPT = json.dumps(
    {"transcript": [{"speaker": "a", "text": "안녕하세요"}, {"speaker": "b", "text": "반가워요"}], "report": NARRATIVE},
    ensure_ascii=False,
)


def test_simulation_retries_once_on_broken_json(monkeypatch):
    calls = _llm_returns_sequence(monkeypatch, ['{"transcript": [', GOOD_SCRIPT])

    out = _run_simulation()

    assert len(calls) == 2
    assert out.report.headline == NARRATIVE["headline"]


def test_simulation_retries_truncated_with_bigger_budget(monkeypatch):
    calls = _llm_returns_sequence(monkeypatch, [LLMError("cut", reason="truncated"), GOOD_SCRIPT])

    _run_simulation(turns=5)

    assert calls[1]["max_tokens"] > calls[0]["max_tokens"]
    assert calls[1]["timeout"] <= calls[0]["timeout"]


def test_simulation_gives_up_after_second_broken_json(monkeypatch):
    calls = _llm_returns_sequence(monkeypatch, ["깨짐", "또 깨짐"])

    with pytest.raises(SimulationFailed) as exc:
        _run_simulation()

    assert len(calls) == 2
    assert exc.value.reason == "invalid_json"


def test_simulation_does_not_retry_timeout(monkeypatch):
    calls = _llm_returns_sequence(monkeypatch, [LLMError("slow", reason="timeout"), GOOD_SCRIPT])

    with pytest.raises(SimulationFailed):
        _run_simulation()

    assert len(calls) == 1


def test_call_timeout_reason_is_timeout(monkeypatch):
    from types import SimpleNamespace

    class SlowCompletions:
        async def create(self, **kwargs):
            await asyncio.sleep(1)
            return SimpleNamespace(choices=[])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SlowCompletions()))
    monkeypatch.setattr(agents, "_get_client", lambda: fake_client)

    with pytest.raises(LLMError) as exc:
        asyncio.run(agents._call(system="s", messages=[], max_tokens=10, timeout=0.01))

    assert exc.value.reason == "timeout"


@pytest.mark.parametrize(
    "reply",
    [
        LLMError("timeout after 120s"),
        "대본을 못 썼어요",
        json.dumps({"transcript": [{"speaker": "a", "text": "안녕"}], "report": NARRATIVE}),
        json.dumps(
            {"transcript": [{"speaker": "a", "text": "안녕"}, {"speaker": "c", "text": "?"}], "report": NARRATIVE}
        ),
        json.dumps({"transcript": [{"speaker": "a", "text": "a"}, {"speaker": "b", "text": "b"}]}),
    ],
    ids=["llm-error", "not-json", "one-line", "unknown-speaker", "no-report"],
)
def test_simulation_bad_llm_output_is_simulation_failed(monkeypatch, reply):
    _llm_returns(monkeypatch, reply)

    with pytest.raises(SimulationFailed):
        _run_simulation()


def test_ideal_fit_with_null_value_does_not_raise():
    """실제로 관측된 사례: 근거 없는 차원을 키를 빼는 대신 null 로 채워 보낸다. 검증에서 안 터져야 한다."""
    payload = {**NARRATIVE, "ideal_fit": {"ideal_warmth": 80, "ideal_status": None}}

    narrative = ReportNarrative.model_validate(payload)

    assert narrative.ideal_fit == {"ideal_warmth": 80, "ideal_status": None}


# ── 화자 뒤바뀜 ─────────────────────────────────────────


def _script(*lines):
    return json.dumps(
        {"transcript": [{"speaker": s, "text": t} for s, t in lines], "report": NARRATIVE}, ensure_ascii=False
    )


MIXED_UP = _script(
    ("a", "안녕하세요 민수예요"), ("b", "반가워요. 지수님은 주말에 뭐 하세요?")
)  # b=지수가 자기 이름으로 상대를 부름


def test_script_where_a_speaker_calls_the_partner_by_own_name_is_generated_again(monkeypatch):
    """a=민수, b=지수. 지수가 '지수님은~'이라고 부르면 화자가 섞인 대본이다 — 한 번 다시 받는다."""
    calls = _llm_returns_sequence(monkeypatch, [MIXED_UP, GOOD_SCRIPT])

    out = _run_simulation()

    assert len(calls) == 2
    assert [line.text for line in out.transcript] == ["안녕하세요", "반가워요"]


def test_script_still_mixed_up_after_retry_is_used_rather_than_failing(monkeypatch):
    """두 번째도 섞였으면 그대로 쓴다 — 품질 문제로 시뮬레이션 전체를 503 으로 실패시키지 않는다."""
    calls = _llm_returns_sequence(monkeypatch, [MIXED_UP, MIXED_UP])

    out = _run_simulation()

    assert len(calls) == 2
    assert out.transcript[1].text == "반가워요. 지수님은 주말에 뭐 하세요?"


def test_partner_called_by_their_own_name_is_not_a_mixup(monkeypatch):
    """a=민수가 '지수님'이라고 부르는 건 정상."""
    calls = _llm_returns_sequence(monkeypatch, [_script(("a", "지수님 반가워요"), ("b", "민수님도요"))])

    _run_simulation()

    assert len(calls) == 1


def test_nickname_containing_the_other_nickname_is_not_a_mixup(monkeypatch):
    """운영 사례(#77): a=셰일, b=내가진짜셰일. 셰일이 "내가진짜셰일님"이라고 부른 건 상대를 제대로 부른 것 —
    "셰일님"이 부분 문자열로 들어 있다고 화자 뒤바뀜으로 보고 정상 대본을 버리면 안 된다."""
    calls = _llm_returns_sequence(
        monkeypatch,
        [_script(("a", "안녕하세요, 내가진짜셰일님. 셰일입니다."), ("b", "셰일님, 안녕하세요."))],
    )

    _run_simulation(name_a="셰일", name_b="내가진짜셰일")

    assert len(calls) == 1


def test_call_cut_off_by_provider_error_is_upstream_error(monkeypatch):
    """운영 사례(#77): HTTP 200 인데 finish_reason=error — 공급자가 생성 도중 끊었다. 반쪽 출력을 파싱하지 않는다."""
    _fake_client(monkeypatch, content='{"transcript": [{"speaker": "a", "text": "안녕', finish_reason="error")

    with pytest.raises(LLMError) as exc:
        asyncio.run(agents._call(system="s", messages=[], max_tokens=10, timeout=5))

    assert exc.value.reason == "upstream_error"


@pytest.mark.parametrize(
    "second",
    [LLMError("cut", reason="upstream_error"), '{"transcript": [{"speaker": "a", "text": "안녕'],
    ids=["provider-error", "broken-json"],
)
def test_first_script_is_used_when_regeneration_for_mixup_fails(monkeypatch, second):
    """화자 뒤바뀜으로 다시 받다가 실패하면 503 대신 1차 대본을 쓴다 — 1차는 파싱·검증을 통과한 대본이다 (#77)."""
    calls = _llm_returns_sequence(monkeypatch, [MIXED_UP, second])

    out = _run_simulation()

    assert len(calls) == 2
    assert out.transcript[1].text == "반가워요. 지수님은 주말에 뭐 하세요?"


def _fake_cut_off_response(monkeypatch):
    """OpenRouter 가 끊긴 응답에 붙이는 필드(provider, choice.error, native_finish_reason)까지 흉내 낸다."""
    from types import SimpleNamespace

    choice = SimpleNamespace(
        message=SimpleNamespace(content='{"transcript": [{"speaker": "a", "text": "안녕'),
        finish_reason="error",
        native_finish_reason="OTHER",
        error={
            "code": 502,
            "message": "upstream disconnected " + "x" * 600,
            "metadata": {"raw": "INTERNAL: stream reset", "provider_name": "Google AI Studio"},
        },
    )
    resp = SimpleNamespace(id="gen-abc", provider="Google AI Studio", choices=[choice], usage=None)

    class FakeCompletions:
        async def create(self, **kwargs):
            return resp

    monkeypatch.setattr(
        agents, "_get_client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    )


def test_call_cut_off_records_provider_error_detail(monkeypatch, caplog):
    """끊긴 사유를 로그·Langfuse 에 남긴다. 503 응답에 실리는 예외 메시지에는 공급자 문구를 넣지 않는다."""
    from types import SimpleNamespace

    _fake_cut_off_response(monkeypatch)
    recorded = []
    monkeypatch.setattr(
        agents, "get_client", lambda: SimpleNamespace(update_current_span=lambda **kw: recorded.append(kw))
    )

    with caplog.at_level("WARNING", logger=agents.__name__), pytest.raises(LLMError) as exc:
        asyncio.run(agents._call(system="s", messages=[], max_tokens=10, timeout=5))

    assert exc.value.reason == "upstream_error"
    assert exc.value.retryable is True
    assert "upstream disconnected" not in str(exc.value)
    detail = recorded[0]["metadata"]["provider_error"]
    assert detail["generation_id"] == "gen-abc"
    assert detail["provider"] == "Google AI Studio"
    assert detail["native_finish_reason"] == "OTHER"
    assert detail["error_code"] == 502
    assert len(detail["error_message"]) == 500
    assert "INTERNAL: stream reset" in detail["error_metadata"]
    assert "gen-abc" in caplog.text and "OTHER" in caplog.text


def test_provider_error_given_as_plain_string_keeps_its_message():
    from types import SimpleNamespace

    choice = SimpleNamespace(error="upstream disconnected")
    detail = agents._provider_error_detail(SimpleNamespace(), choice)

    assert detail["error_message"] == "upstream disconnected"
    assert detail["error_code"] is None


def test_call_cut_off_still_fails_cleanly_when_langfuse_recording_breaks(monkeypatch):
    _fake_cut_off_response(monkeypatch)

    def broken():
        raise RuntimeError("langfuse down")

    monkeypatch.setattr(agents, "get_client", broken)

    with pytest.raises(LLMError) as exc:
        asyncio.run(agents._call(system="s", messages=[], max_tokens=10, timeout=5))

    assert exc.value.reason == "upstream_error"


def test_call_cut_off_without_error_body_still_has_detail_keys(monkeypatch):
    """choice.error 가 없는 응답(기존 _fake_client)도 그대로 upstream_error 다."""
    _fake_client(monkeypatch, content='{"transcript": [', finish_reason="error")
    monkeypatch.setattr(agents, "_record_provider_error", lambda detail: None)

    with pytest.raises(LLMError) as exc:
        asyncio.run(agents._call(system="s", messages=[], max_tokens=10, timeout=5))

    assert exc.value.retryable is True


def test_simulation_retries_once_when_provider_cuts_off(monkeypatch):
    """운영 사례(2026-10-02): 생성 도중 끊긴 요청이 몇 초 뒤 같은 입력으로 성공했다."""
    cut = LLMError("provider error mid-generation", reason="upstream_error", retryable=True)
    calls = _llm_returns_sequence(monkeypatch, [cut, GOOD_SCRIPT])

    out = _run_simulation()

    assert len(calls) == 2
    assert out.report.headline == NARRATIVE["headline"]
    assert calls[1]["timeout"] <= calls[0]["timeout"]


def test_simulation_gives_up_after_second_provider_cut_off(monkeypatch):
    cut = LLMError("provider error mid-generation", reason="upstream_error", retryable=True)
    calls = _llm_returns_sequence(monkeypatch, [cut, cut])

    with pytest.raises(SimulationFailed) as exc:
        _run_simulation()

    assert len(calls) == 2
    assert exc.value.reason == "upstream_error"


def test_simulation_does_not_retry_upstream_status_error(monkeypatch):
    """4xx/5xx 는 SDK 가 이미 재시도했다 — 같은 upstream_error 라도 다시 보내지 않는다."""
    calls = _llm_returns_sequence(monkeypatch, [LLMError("upstream error 502", reason="upstream_error"), GOOD_SCRIPT])

    with pytest.raises(SimulationFailed) as exc:
        _run_simulation()

    assert len(calls) == 1
    assert exc.value.reason == "upstream_error"
