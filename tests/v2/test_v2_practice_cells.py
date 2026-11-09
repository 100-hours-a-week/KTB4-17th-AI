"""연습대화 애착 9칸 라우팅 — 칸 고르기, 학습 형식 프롬프트, 세션 고정, 서버가 죽었을 때 메인 LLM 으로 내리기."""

import asyncio
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.core import slm
from app.core.config import get_settings
from app.features.persona.schemas import PersonaRef, PersonaResponse
from app.features.practice.schemas import PracticeStartRequest
from app.v2.practice import agents, cells
from app.v2.practice.agents import LLMError, PartnerAgent, SlmRoute
from app.v2.practice.service import V2PracticeService
from tests.conftest import seed_persona, with_db
from tests.v2.test_v2_practice_api import FakeEmbedder


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("PRACTICE_SLM_BASE_URL", "https://cells-8000.proxy.runpod.test/v1")
    monkeypatch.setenv("SLM_PROBE_INTERVAL_S", "0.01")
    monkeypatch.setenv("GUARDRAIL_MODE", "off")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _health(monkeypatch, up: bool):
    async def check():
        return up

    h = slm.Health(check=check, name="test cells")
    h.up = up
    monkeypatch.setattr(cells, "health", h)
    return h


def _down_error() -> LLMError:
    cause = openai.APIConnectionError(request=httpx.Request("POST", "https://cells-8000.proxy.runpod.test/v1"))
    try:
        raise LLMError("connection refused") from cause
    except LLMError as e:
        return e


class ScriptedAgent(PartnerAgent):
    """reply 호출을 기록하고, 9칸 호출이면 slm_outcome(예외면 raise), 메인 LLM 이면 정해진 답을 낸다."""

    def __init__(self, slm_outcome="9칸 답이에요") -> None:
        self.calls: list[dict] = []
        self.slm_outcome = slm_outcome  # 문자열·예외 하나, 또는 차례로 낼 목록

    async def reply(self, *, system, history, opening=False, trace_metadata=None, slm=None):
        self.calls.append({"system": system, "slm": slm, "history": list(history), "opening": opening})
        if slm is not None:
            outcome = self.slm_outcome.pop(0) if isinstance(self.slm_outcome, list) else self.slm_outcome
            if isinstance(outcome, BaseException):
                raise outcome
            yield outcome
        else:
            yield "제미나이 답이에요"


# ── 칸 고르기 ──────────────────────────────────────────────


def _persona(**scores) -> PersonaResponse:
    return PersonaResponse(persona_id="p", scores=scores)


@pytest.mark.parametrize(
    ("avoidance", "anxiety", "cell"),
    [
        (20, 10, "low_anx0"),
        (35, 36, "low_anx0.75"),
        (36, 64, "mid_anx0.75"),
        (50, 65, "mid_anx1"),
        (72, 50, "high_anx0.75"),
        (65, 90, "high_anx1"),
        (None, None, "mid_anx0"),
        (80, None, "high_anx0"),
    ],
)
def test_cell_for_maps_scores_to_cell_names(avoidance, anxiety, cell):
    assert cells.cell_for(_persona(avoidance=avoidance, anxiety=anxiety)) == cell


def test_slm_prompt_matches_training_format_without_attachment_lines():
    p = PersonaResponse(
        persona_id="p",
        mbti="INFP",
        scores={"avoidance": 90, "anxiety": 90, "contact_rhythm": 90, "openness": 80, "ideal_warmth": 20},
    )
    prompt = cells.system_prompt(partner_name="찬우", partner=p, my_name="우진")

    assert prompt.startswith("너는 아래 프로필의 인물 본인으로서 우진와 1:1 메신저로 대화한다.")
    assert "[프로필]\n찬우 / INFP\n말투: 존댓말, 적당한 길이, 감정 자연스러움, 대화 주도 보통" in prompt
    assert "성향: 새로운 장소나 색다른 활동을 먼저 찾아봄" in prompt
    assert "이상형: 감정 표현이 과하지 않고 담백한 사람" in prompt
    assert prompt.endswith("[상대] 우진")
    with_mbti = cells.system_prompt(partner_name="찬우", partner=p, my_name="우진", my_mbti="ENFP")
    assert with_mbti.endswith("[상대] 우진 (ENFP)")
    # 애착·연락 성향 줄은 칸이 정한다 — 프롬프트에 넣지 않는다
    assert "거리" not in prompt and "연락" not in prompt and "신경" not in prompt


# ── 세션 고정과 라우팅 ─────────────────────────────────────


async def _start(factory, agent, *, scores=None):
    db = factory()
    await seed_persona(db, persona_id="p-partner", user_id="pu1", nickname="지민", scores=scores)
    service = V2PracticeService(db, embedder=FakeEmbedder())
    service.agent = agent
    started = await service.start(PracticeStartRequest(partner_user_id="pu1", nickname="민수"))
    await db.commit()
    return db, service, await service.repo.get_session(started.session_id)


def _done(events):
    return [data for name, data in events if name == "done"][0]


def test_session_started_while_server_up_uses_its_cell(monkeypatch):
    _health(monkeypatch, up=True)

    async def body(factory):
        agent = ScriptedAgent()
        db, service, session = await _start(factory, agent, scores={"avoidance": 72, "anxiety": 55})
        assert (session.reply_engine, session.slm_cell) == ("slm", "high_anx0.75")

        events = [e async for e in service.stream_reply(session, "주말에 뭐 하셨어요?")]
        call = agent.calls[0]
        assert call["slm"] == SlmRoute(cell="high_anx0.75")
        assert call["system"].startswith("너는 아래 프로필의 인물 본인으로서")
        assert _done(events).content == "9칸 답이에요"
        assert _done(events).source == "llm"  # API 는 그대로
        assert session.messages[-1].source == "slm"  # DB 에는 9칸으로 남는다
        await db.close()

    asyncio.run(with_db(body))


def test_session_started_while_server_down_stays_on_main_llm_after_recovery(monkeypatch):
    health = _health(monkeypatch, up=False)

    async def body(factory):
        agent = ScriptedAgent()
        db, service, session = await _start(factory, agent)
        assert (session.reply_engine, session.slm_cell) == ("llm", None)

        health.up = True  # 대화 도중 Pod 가 켜졌다
        events = [e async for e in service.stream_reply(session, "안녕하세요")]
        assert agent.calls[0]["slm"] is None
        assert "## 말하는 방식" in agent.calls[0]["system"]  # 메인 LLM 프롬프트
        assert _done(events).content == "제미나이 답이에요"
        await db.close()

    asyncio.run(with_db(body))


def test_slm_failure_before_first_chunk_retries_turn_on_main_llm_and_demotes_session(monkeypatch):
    _health(monkeypatch, up=True)

    async def body(factory):
        agent = ScriptedAgent(slm_outcome=_down_error())
        db, service, session = await _start(factory, agent)

        events = [e async for e in service.stream_reply(session, "안녕하세요")]
        assert [c["slm"] is not None for c in agent.calls] == [True, False]
        assert "## 말하는 방식" in agent.calls[1]["system"]  # 다시 보낼 때는 메인 LLM 프롬프트
        assert _done(events).content == "제미나이 답이에요"
        assert session.messages[-1].source == "llm"
        assert session.reply_engine == "llm" and session.slm_cell == "mid_anx0"
        assert cells.health.up is False  # 다음 세션도 메인 LLM 으로 시작한다

        # 서버가 살아나도 이 세션은 돌아가지 않는다
        cells.health.up = True
        agent.slm_outcome = "9칸 답이에요"
        _ = [e async for e in service.stream_reply(session, "또 와요")]
        assert agent.calls[-1]["slm"] is None
        await cells.health.stop()
        await db.close()

    asyncio.run(with_db(body))


def test_slm_session_with_server_marked_down_goes_to_main_llm(monkeypatch):
    health = _health(monkeypatch, up=True)

    async def body(factory):
        agent = ScriptedAgent()
        db, service, session = await _start(factory, agent)
        health.up = False  # 세션 사이에 Pod 가 꺼졌다 (다른 요청이 실패를 봤다)

        _ = [e async for e in service.stream_reply(session, "안녕하세요")]
        assert [c["slm"] for c in agent.calls] == [None]
        assert session.reply_engine == "llm"
        await db.close()

    asyncio.run(with_db(body))


def test_without_base_url_sessions_use_main_llm(monkeypatch):
    monkeypatch.setenv("PRACTICE_SLM_BASE_URL", "")
    get_settings.cache_clear()
    _health(monkeypatch, up=True)

    async def body(factory):
        db, _, session = await _start(factory, ScriptedAgent())
        assert session.reply_engine == "llm"
        await db.close()

    asyncio.run(with_db(body))


def test_partner_lookup_still_uses_session_persona(monkeypatch):
    """9칸 프롬프트도 세션에 고정된 상대 페르소나 버전으로 만든다."""
    _health(monkeypatch, up=True)

    async def body(factory):
        agent = ScriptedAgent()
        db, service, session = await _start(factory, agent)
        _ = [e async for e in service.stream_reply(session, "안녕하세요")]
        loaded = await service._load("partner", PersonaRef(persona_id=session.partner_persona_id))
        assert loaded.record.id == "p-partner"
        assert agent.calls[0]["system"].count("[프로필]\n지민") == 1
        await db.close()

    asyncio.run(with_db(body))


def test_opening_and_replies_go_to_cell_and_cell_sees_greeting_without_instruction(monkeypatch):
    """첫 인사도 9칸. 다음 턴 9칸 기록에는 인사 지시문을 빼고 상대 인사(assistant)부터 보낸다."""
    _health(monkeypatch, up=True)

    async def body(factory):
        agent = ScriptedAgent(slm_outcome=["안녕하세요, 반가워요", "네, 저도요"])
        db, service, session = await _start(factory, agent)
        _ = [e async for e in service.stream_opening(session)]
        _ = [e async for e in service.stream_reply(session, "안녕하세요")]

        opening, reply = agent.calls
        assert opening["slm"] == reply["slm"] == SlmRoute(cell="mid_anx0")
        assert opening["opening"] is True and opening["history"] == []
        assert reply["history"] == [
            {"role": "assistant", "content": "안녕하세요, 반가워요"},
            {"role": "user", "content": "안녕하세요"},
        ]
        assert [m.source for m in session.messages if m.role == "persona"] == ["slm", "slm"]
        await db.close()

    asyncio.run(with_db(body))


def test_guardrail_regenerate_on_cell_resamples_without_notice(monkeypatch):
    """9칸 답이 가드레일 재생성 대상이면, 위반 지시문 없이 같은 입력으로 9칸에서 한 번 더 뽑는다."""
    _health(monkeypatch, up=True)
    monkeypatch.setenv("GUARDRAIL_MODE", "enforce")

    async def body(factory):
        agent = ScriptedAgent(slm_outcome=["지민님은 그렇게 생각해요", "저는 그렇게 생각해요"])
        db, service, session = await _start(factory, agent)
        events = [e async for e in service.stream_reply(session, "어떻게 생각해요?")]

        first, retry = agent.calls
        assert first["slm"] == retry["slm"] == SlmRoute(cell="mid_anx0")
        assert retry["history"] == first["history"]  # 지시문(CORRECTION NOTICE)을 붙이지 않는다
        assert retry["system"] == first["system"]
        done = _done(events)
        assert done.content == "저는 그렇게 생각해요"
        assert done.validationResult.status == "REGENERATED"
        await db.close()

    asyncio.run(with_db(body))


def test_guardrail_regenerate_on_main_llm_still_gets_notice(monkeypatch):
    _health(monkeypatch, up=False)
    monkeypatch.setenv("GUARDRAIL_MODE", "enforce")

    class MainOnly(ScriptedAgent):
        async def reply(self, *, system, history, opening=False, trace_metadata=None, slm=None):
            self.calls.append({"history": list(history), "slm": slm})
            yield "지민님은 그렇게 생각해요" if len(self.calls) == 1 else "저는 그렇게 생각해요"

    async def body(factory):
        agent = MainOnly()
        db, service, session = await _start(factory, agent)
        _ = [e async for e in service.stream_reply(session, "어떻게 생각해요?")]
        assert "[CORRECTION NOTICE]" in agent.calls[1]["history"][-1]["content"]
        await db.close()

    asyncio.run(with_db(body))


# ── 스트리밍 호출 ─────────────────────────────────────────


def _stream_client(chunks, delay=0.0):
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)

        async def gen():
            await asyncio.sleep(delay)
            for c in chunks:
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=c))])

        return gen()

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), calls


def test_stream_to_slm_sends_cell_name_with_training_sampling(monkeypatch):
    client, calls = _stream_client(["안녕", "하세요"])
    monkeypatch.setattr(cells, "get_client", lambda: client)

    async def run():
        return [c async for c in PartnerAgent().reply(system="S", history=[], slm=SlmRoute(cell="low_anx1"))]

    assert asyncio.run(run()) == ["안녕", "하세요"]
    assert calls[0]["model"] == "low_anx1"
    assert calls[0]["temperature"] == 0.7
    assert calls[0]["max_tokens"] == 160
    assert len(calls) == 1


def test_slm_first_chunk_timeout_is_short_and_not_retried(monkeypatch):
    monkeypatch.setenv("PRACTICE_SLM_FIRST_CHUNK_TIMEOUT_S", "0.05")
    get_settings.cache_clear()
    client, calls = _stream_client(["늦은 답"], delay=1.0)
    monkeypatch.setattr(cells, "get_client", lambda: client)

    async def run():
        return [c async for c in PartnerAgent().reply(system="S", history=[], slm=SlmRoute(cell="mid_anx0"))]

    with pytest.raises(LLMError) as info:
        asyncio.run(run())
    assert isinstance(info.value.__cause__, TimeoutError)
    assert len(calls) == 1


def test_main_llm_stream_is_unchanged(monkeypatch):
    client, calls = _stream_client(["답"])
    monkeypatch.setattr(agents, "_get_client", lambda: client)

    async def run():
        return [c async for c in PartnerAgent().reply(system="S", history=[])]

    assert asyncio.run(run()) == ["답"]
    assert calls[0]["model"] == get_settings().llm_model_v2
    assert calls[0]["max_tokens"] == 300
    assert "temperature" not in calls[0]
