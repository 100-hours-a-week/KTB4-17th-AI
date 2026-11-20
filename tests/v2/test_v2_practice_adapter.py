"""연습대화 말투 어댑터 — 페르소나 주인의 ACTIVE 어댑터 고르기, 학습 때와 같은 요청, 실패한 턴의 기존 경로 폴백."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.core import slm
from app.core.config import get_settings
from app.features.mlops import serving
from app.features.mlops.models import PersonaAdapter
from app.features.persona.lookup import load_persona
from app.features.persona.profile import describe
from app.features.persona.schemas import PersonaRef
from app.features.practice.schemas import PracticeStartRequest
from app.v2.practice import cells
from app.v2.practice.agents import PartnerAgent, SlmRoute
from app.v2.practice.service import V2PracticeService
from tests.conftest import seed_persona, with_db
from tests.v2.test_v2_practice_api import FakeEmbedder

GOLDEN = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "mlops_prompt_golden.json").read_text(encoding="utf-8")
)
BASE = "org/persona-base@80af84e"
NOW = datetime(2026, 11, 20, 1, 0, tzinfo=UTC)

# 골든 기대값의 personas.지민 을 만든 페르소나 (tests/fixtures/mlops_prompt_golden.json)
OWNER = dict(
    persona_id="p-owner",
    user_id="owner-1",
    nickname="지민",
    mbti="ENFP",
    scores={"avoidance": 20, "anxiety": 70, "contact_rhythm": 85, "openness": 50, "seriousness": 30},
    confidence={
        "avoidance": "HIGH",
        "anxiety": "LOW",
        "contact_rhythm": "MEDIUM",
        "openness": "MEDIUM",
        "seriousness": "HIGH",
    },
    texts={
        "interests": ["러닝", "전시 보기"],
        "routine": ["퇴근 후 한강 산책"],
        "date_prefer": ["맛집 탐방"],
        "date_avoid": ["시끄러운 술자리"],
    },
)
NARRATIVE = {
    "headline": "지민 (ENFP) - 먼저 연락하는 다정한 사람",
    "body": "작은 일도 공유하며 가까워지는 걸 좋아하는 캐릭터",
    "traits": ["contact_rhythm (매우 높음)", "avoidance (매우 낮음)"],
}
STYLE = {
    "frequent_phrases": ["진짜요?"],
    "endings": ["~요", "~죠"],
    "laughter": "ㅎㅎ 자주",
    "summary": "밝고 짧게 반응",
}

MAIN_REPLY = "제미나이 답이에요"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MLOPS_ADAPTER_ENABLED", "true")
    monkeypatch.setenv("MLOPS_SERVING_BASE_URL", "https://serving.test/v1")
    monkeypatch.setenv("MLOPS_SERVING_BASE_MODEL_ID", BASE)
    monkeypatch.setenv("MLOPS_ADAPTER_USER_ALLOWLIST", "")
    monkeypatch.setenv("MLOPS_ADAPTER_TEST_NICKNAME_MAP", "")
    monkeypatch.setenv("GUARDRAIL_MODE", "off")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


class MainAgent(PartnerAgent):
    """기존 경로(9칸·메인 LLM) 호출을 기록한다."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def reply(self, *, system, history, opening=False, trace_metadata=None, slm=None):
        self.calls.append({"system": system, "history": list(history), "slm": slm})
        yield "9칸 답이에요" if slm is not None else MAIN_REPLY


class FakeServing:
    """서빙 vLLM 대신. outcomes 를 차례로 낸다 (마지막 것은 계속). 예외면 raise, "SLOW" 면 오래 기다린다."""

    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome == "SLOW":
            await asyncio.sleep(1.0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=outcome))])


def _serve(monkeypatch, *outcomes) -> FakeServing:
    fake = FakeServing(outcomes or ["지민(나): 어댑터 답이에요"])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake.create)))
    monkeypatch.setattr(serving, "get_client", lambda: client)
    return fake


def _row(user_id="owner-1", version=2, status="active", base=BASE) -> PersonaAdapter:
    return PersonaAdapter(
        user_id=user_id,
        version=version,
        status=status,
        base_model_id=base,
        sample_count=300,
        metrics={},
        artifact_uri=f"hf://org/persona-adapters/{user_id}/v{version}",
        created_at=NOW,
        activated_at=NOW if status == "active" else None,
    )


async def _start(factory, *, rows=(), me=False, agent=None):
    """페르소나 주인 지민(owner-1) 과 연습한다. me=True 면 연습하는 사람도 페르소나(me-1, 민수)가 있다."""
    db = factory()
    record = await seed_persona(db, **OWNER)
    record.narrative = NARRATIVE
    record.conversation_style = STYLE
    if me:
        await seed_persona(db, persona_id="p-me", user_id="me-1", nickname="민수")
    db.add_all(list(rows))
    await db.commit()
    service = V2PracticeService(db, embedder=FakeEmbedder())
    service.agent = agent or MainAgent()
    req = PracticeStartRequest(partner_user_id="owner-1", me_user_id="me-1") if me else None
    started = await service.start(req or PracticeStartRequest(partner_user_id="owner-1", nickname="민수"))
    await db.commit()
    return db, service, await service.repo.get_session(started.session_id)


def _done(events):
    return [data for name, data in events if name == "done"][0]


def _deltas(events):
    return [data.text for name, data in events if name == "delta"]


# ── 고르기 ─────────────────────────────────────────────────


def test_owner_active_adapter_is_fixed_on_session(monkeypatch):
    async def body(factory):
        db, _, session = await _start(factory, rows=[_row(version=1, status="ready"), _row(version=2)])
        assert session.adapter_model == "owner-1-v2"
        assert session.partner_user_id == "owner-1"
        assert session.reply_engine == "llm"  # 기존 경로도 그대로 정해 둔다 (실패한 턴에 쓴다)
        await db.close()

    asyncio.run(with_db(body))


def test_practicing_users_adapter_is_not_used(monkeypatch):
    """어댑터는 페르소나 주인(partner_user_id)의 것이다. 연습하는 사람(user_id)의 어댑터는 보지 않는다."""

    async def body(factory):
        db, _, session = await _start(factory, rows=[_row(user_id="me-1", version=1)], me=True)
        assert session.user_id == "me-1"
        assert session.adapter_model is None
        await db.close()

    asyncio.run(with_db(body))

    async def both(factory):
        rows = [_row(user_id="me-1", version=1), _row(user_id="owner-1", version=3)]
        db, _, session = await _start(factory, rows=rows, me=True)
        assert session.adapter_model == "owner-1-v3"
        await db.close()

    asyncio.run(with_db(both))


@pytest.mark.parametrize(
    "rows",
    [
        [],  # 아직 학습 전
        [_row(status="ready"), _row(version=3, status="failed")],  # active 가 없다
        [_row(base="org/persona-base@old")],  # 다른 베이스로 학습한 어댑터
    ],
    ids=["no_rows", "not_active", "base_mismatch"],
)
def test_no_usable_active_adapter_keeps_existing_path(rows):
    async def body(factory):
        db, service, session = await _start(factory, rows=rows)
        assert session.adapter_model is None
        events = [e async for e in service.stream_reply(session, "안녕하세요")]
        assert _done(events).content == MAIN_REPLY
        assert session.messages[-1].source == "llm"
        await db.close()

    asyncio.run(with_db(body))


@pytest.mark.parametrize(
    ("allowlist", "expected"),
    [("", "owner-1-v2"), ("owner-1", "owner-1-v2"), (" other-9 , owner-1 ", "owner-1-v2"), ("other-9", None)],
)
def test_allowlist_is_checked_against_persona_owner(monkeypatch, allowlist, expected):
    _set(monkeypatch, MLOPS_ADAPTER_USER_ALLOWLIST=allowlist)

    async def body(factory):
        db, _, session = await _start(factory, rows=[_row()])
        assert session.adapter_model == expected
        await db.close()

    asyncio.run(with_db(body))


@pytest.mark.parametrize(
    "env",
    [
        {"MLOPS_ADAPTER_ENABLED": "false"},
        {"MLOPS_SERVING_BASE_URL": ""},
        {"MLOPS_SERVING_BASE_MODEL_ID": ""},
    ],
    ids=["disabled", "no_base_url", "no_base_model_id"],
)
def test_disabled_or_unconfigured_never_selects(monkeypatch, env):
    _set(monkeypatch, **env)
    fake = _serve(monkeypatch)

    async def body(factory):
        db, service, session = await _start(factory, rows=[_row()])
        assert session.adapter_model is None
        _ = [e async for e in service.stream_reply(session, "안녕하세요")]
        assert fake.calls == []
        await db.close()

    asyncio.run(with_db(body))


def test_lookup_failure_still_starts_session(monkeypatch):
    async def broken(db, user_id):
        raise RuntimeError("relation persona_adapter does not exist")

    monkeypatch.setattr(serving, "get_active", broken)

    async def body(factory):
        db, _, session = await _start(factory, rows=[_row()])
        assert session.adapter_model is None
        assert session.id
        await db.close()

    asyncio.run(with_db(body))


# ── [임시 시험용] 닉네임 표 ──────────────────────────────────


def test_trial_nickname_map_parsing(monkeypatch):
    _set(
        monkeypatch,
        MLOPS_ADAPTER_TEST_NICKNAME_MAP=(
            " 반갑맨=couple_user_02-v1 , 노엘 = couple_user_01-v1,,이름만,=x-v1,빈값=,공백=a b,등호=a=b,"
            f"긴이름={'x' * 97},노엘=couple_user_01-v2"
        ),
    )
    assert serving.trial_nickname_map() == {"반갑맨": "couple_user_02-v1", "노엘": "couple_user_01-v2"}


@pytest.mark.parametrize(
    ("mapping", "expected"),
    [
        ("지민=couple_user_02-v1", "couple_user_02-v1"),
        (" 지민 = couple_user_02-v1 ", "couple_user_02-v1"),  # 항목의 앞뒤 공백은 걷는다
        ("지민님=couple_user_02-v1", None),  # 완전히 같을 때만
        ("지=couple_user_02-v1", None),
        ("민수=couple_user_02-v1", None),  # 연습하는 사람의 닉네임은 보지 않는다
        ("지민 쓰기=couple_user_02-v1,잘못된항목", None),
    ],
)
def test_trial_nickname_map_matches_partner_nickname_exactly(monkeypatch, mapping, expected):
    _set(monkeypatch, MLOPS_ADAPTER_TEST_NICKNAME_MAP=mapping)

    async def body(factory):
        db, _, session = await _start(factory)  # DB 에 어댑터 행이 없다
        assert session.partner_nickname == "지민"
        assert session.adapter_model == expected
        await db.close()

    asyncio.run(with_db(body))


def test_trial_nickname_map_wins_over_db_and_skips_base_check(monkeypatch):
    _set(
        monkeypatch,
        MLOPS_ADAPTER_TEST_NICKNAME_MAP="지민=couple_user_02-v1",
        MLOPS_SERVING_BASE_MODEL_ID="",  # 베이스를 몰라도 시험 표는 동작한다
        MLOPS_ADAPTER_USER_ALLOWLIST="other-9",  # 허용 목록도 보지 않는다
    )
    fake = _serve(monkeypatch, "지민(나): 시험 어댑터 답이에요")

    async def body(factory):
        db, service, session = await _start(factory, rows=[_row(base="org/persona-base@other")])
        assert session.adapter_model == "couple_user_02-v1"
        events = [e async for e in service.stream_reply(session, "안녕하세요")]
        assert fake.calls[0]["model"] == "couple_user_02-v1"
        assert _done(events).content == "시험 어댑터 답이에요"
        await db.close()

    asyncio.run(with_db(body))


def test_trial_nickname_map_needs_enabled_flag(monkeypatch):
    _set(monkeypatch, MLOPS_ADAPTER_TEST_NICKNAME_MAP="지민=couple_user_02-v1", MLOPS_ADAPTER_ENABLED="false")

    async def body(factory):
        db, _, session = await _start(factory)
        assert session.adapter_model is None
        await db.close()

    asyncio.run(with_db(body))


def test_trial_nickname_map_session_falls_back_on_unknown_adapter(monkeypatch):
    _set(monkeypatch, MLOPS_ADAPTER_TEST_NICKNAME_MAP="지민=couple_user_02-v1")
    request = httpx.Request("POST", "https://serving.test/v1/chat/completions")
    fake = _serve(
        monkeypatch, openai.NotFoundError("model not found", response=httpx.Response(404, request=request), body=None)
    )

    async def body(factory):
        db, service, session = await _start(factory)
        events = [e async for e in service.stream_reply(session, "안녕하세요")]
        assert len(fake.calls) == 1
        assert _done(events).content == MAIN_REPLY
        await db.close()

    asyncio.run(with_db(body))


# ── 학습 때와 같은 요청 ─────────────────────────────────────


def test_requests_match_training_prompt_and_label_is_stripped(monkeypatch):
    """페르소나 주인 = 본인(나), 연습하는 사람 = 상대. 요청 messages 가 mlops 빌더 기대값과 글자까지 같다."""
    fake = _serve(
        monkeypatch,
        "지민(나): 안녕하세요! 오늘 날씨 진짜 좋죠?",
        "지민(나): 오 한강이요? 저도 퇴근하고 자주 걸어요\n민수(상대): 좋네요",  # 상대 대사를 이어 쓰면 잘라낸다
        "지민(나): 주말엔 러닝해요 ㅎㅎ",
    )
    cases = {c["name"]: c for c in GOLDEN["cases"]}

    async def body(factory):
        agent = MainAgent()
        db, service, session = await _start(factory, rows=[_row()], agent=agent)
        loaded = await load_persona(db, PersonaRef(persona_id="p-owner"))
        # 프로필 블록은 v1 describe() — persona-mlops 학습 프로필과 같은 형식
        assert describe(loaded.nickname, loaded.response) == GOLDEN["personas"]["지민"]

        opening = [e async for e in service.stream_opening(session)]
        first = [e async for e in service.stream_reply(session, "네 ㅎㅎ 저는 아까 한강 다녀왔어요")]
        second = [e async for e in service.stream_reply(session, "  주말에는 뭐 하세요?  ")]

        assert fake.calls[0]["messages"] == cases["opening"]["expected"]
        assert fake.calls[2]["messages"] == cases["reply"]["expected"]
        for call in fake.calls:
            assert call["model"] == "owner-1-v2"
            assert (call["temperature"], call["top_p"], call["max_tokens"]) == (0.7, 0.95, 160)
            assert call["name"] == "practice-reply-adapter"
            assert "stream" not in call

        assert _deltas(opening) == ["안녕하세요! 오늘 날씨 진짜 좋죠?"]  # 라벨 없이 한 번에
        assert _done(first).content == "오 한강이요? 저도 퇴근하고 자주 걸어요"
        assert _done(second).content == "주말엔 러닝해요 ㅎㅎ"
        assert _done(second).source == "llm"  # API 는 그대로 llm | fallback
        persona = [m for m in session.messages if m.role == "persona"]
        assert [m.source for m in persona] == ["adapter", "adapter", "adapter"]
        assert agent.calls == []  # 기존 경로는 부르지 않았다
        await db.close()

    asyncio.run(with_db(body))


def test_guardrail_regenerate_resamples_adapter_with_same_request(monkeypatch):
    _set(monkeypatch, GUARDRAIL_MODE="enforce")
    fake = _serve(monkeypatch, "지민(나): 지민님은 그렇게 생각해요", "지민(나): 저는 그렇게 생각해요")

    async def body(factory):
        db, service, session = await _start(factory, rows=[_row()])
        events = [e async for e in service.stream_reply(session, "어떻게 생각해요?")]
        assert len(fake.calls) == 2
        assert fake.calls[1]["messages"] == fake.calls[0]["messages"]  # 위반 지시문을 붙이지 않는다
        done = _done(events)
        assert done.content == "저는 그렇게 생각해요"
        assert done.validationResult.status == "REGENERATED"
        assert session.messages[-1].source == "adapter"
        await db.close()

    asyncio.run(with_db(body))


# ── 실패하면 그 턴만 기존 경로 ───────────────────────────────

_REQUEST = httpx.Request("POST", "https://serving.test/v1/chat/completions")


@pytest.mark.parametrize(
    "outcome",
    [
        openai.APIConnectionError(request=_REQUEST),
        openai.NotFoundError("model not found", response=httpx.Response(404, request=_REQUEST), body=None),
        RuntimeError("anything else"),
        "SLOW",  # MLOPS_SERVING_TIMEOUT_S 를 넘긴다
        "민수(상대): 제가 대신 말해요",  # 라벨을 떼면 남는 게 없다
        "",
    ],
    ids=["connection", "unknown_adapter", "other_error", "timeout", "partner_label_only", "empty"],
)
def test_serving_failure_falls_back_for_that_turn_only(monkeypatch, outcome):
    _set(monkeypatch, MLOPS_SERVING_TIMEOUT_S="0.05")
    fake = _serve(monkeypatch, outcome, "지민(나): 다시 어댑터예요")

    async def body(factory):
        agent = MainAgent()
        db, service, session = await _start(factory, rows=[_row()], agent=agent)

        events = [e async for e in service.stream_reply(session, "안녕하세요")]
        assert _done(events).content == MAIN_REPLY
        assert _done(events).source == "llm"
        assert session.messages[-1].source == "llm"
        assert len(agent.calls) == 1 and agent.calls[0]["slm"] is None
        assert "## 말하는 방식" in agent.calls[0]["system"]  # 기존 메인 LLM 프롬프트 그대로
        assert session.adapter_model == "owner-1-v2"  # 세션은 내리지 않는다

        # 다음 턴은 다시 어댑터로 간다
        events = [e async for e in service.stream_reply(session, "또 와요")]
        assert len(fake.calls) == 2
        assert _done(events).content == "다시 어댑터예요"
        assert session.messages[-1].source == "adapter"
        await db.close()

    asyncio.run(with_db(body))


def test_failure_on_cell_session_falls_back_to_cell_without_demoting(monkeypatch):
    """기존 경로가 9칸이면 9칸으로 답한다 (기존 경로는 바뀌지 않는다)."""
    _set(monkeypatch, PRACTICE_SLM_BASE_URL="https://cells-8000.proxy.runpod.test/v1")
    health = slm.Health(check=lambda: asyncio.sleep(0, result=True), name="test cells")
    monkeypatch.setattr(cells, "health", health)
    _serve(monkeypatch, openai.APIConnectionError(request=_REQUEST))

    async def body(factory):
        agent = MainAgent()
        db, service, session = await _start(factory, rows=[_row()], agent=agent)
        assert (session.reply_engine, session.adapter_model) == ("slm", "owner-1-v2")
        events = [e async for e in service.stream_reply(session, "안녕하세요")]
        assert agent.calls[0]["slm"] == SlmRoute(cell=session.slm_cell)
        assert _done(events).content == "9칸 답이에요"
        assert session.messages[-1].source == "slm"
        assert session.reply_engine == "slm"
        await db.close()

    asyncio.run(with_db(body))


def test_turning_flag_off_mid_session_uses_existing_path(monkeypatch):
    fake = _serve(monkeypatch)

    async def body(factory):
        agent = MainAgent()
        db, service, session = await _start(factory, rows=[_row()], agent=agent)
        assert session.adapter_model == "owner-1-v2"
        _set(monkeypatch, MLOPS_ADAPTER_ENABLED="false")
        events = [e async for e in service.stream_reply(session, "안녕하세요")]
        assert fake.calls == []
        assert _done(events).content == MAIN_REPLY
        await db.close()

    asyncio.run(with_db(body))
