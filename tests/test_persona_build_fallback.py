"""/build 폴백 — 추출 LLM 이 실패해도 온보딩은 끝나야 한다.

LLM 없이 규칙으로 만든 초안(source="fallback")을 돌려주고, 다음 /build 때 LLM 추출을 다시 시도한다.
"""

import asyncio
from types import SimpleNamespace

import pytest
from conftest import with_db
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.features.persona import api
from app.features.persona.agents import BuildFailed, ExtractionAgent
from app.features.persona.models import OnboardingSession, OnboardingTurn
from app.features.persona.repository import PersonaRepository
from app.features.persona.schemas import PersonaResponse, RawExtraction, Summary
from app.features.persona.service import OnboardingService, valid_summaries


class FakeExtraction(ExtractionAgent):
    """추출 LLM 자리. results 를 차례로 쓴다 — 예외면 raise, 아니면 그 추출 결과."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    async def extract(self, history, *, trace_metadata=None):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


DOWN = BuildFailed("LLM call failed: timeout")
GOOD = RawExtraction(avoidance=78, seriousness=90, interests=["러닝"])


def _session(orientation_answer="진지하게 만날 사람") -> OnboardingSession:
    """10턴을 다 마친 온보딩. 마지막 턴은 선택지로 답하는 관계 진지도 질문."""
    turns = [
        OnboardingTurn(turn_index=0, topic_id="interests", question="요즘 뭐 하면서 지내요?", answer="러닝해요"),
        OnboardingTurn(turn_index=9, topic_id="orientation", question="어떤 연애?", answer=orientation_answer),
    ]
    return OnboardingSession(
        id="session-1",
        user_id="user-1",
        nickname="민수",
        total_turns=10,
        turn_index=10,
        pending_topic_id=None,
        used_topic_ids=["interests", "orientation"],
        coverage={"primary": {"interests": 1, "seriousness": 1}, "secondary": {}},
        status="completed",
        turns=turns,
    )


def _build(*extraction_results, orientation_answer="진지하게 만날 사람", builds=1):
    """/build 를 builds 번 부른다. 요청마다 새 DB 세션."""

    async def scenario(factory):
        async with factory() as db:
            db.add(_session(orientation_answer))
            await db.commit()
        agent = FakeExtraction(*extraction_results)
        responses = []
        for _ in range(builds):
            async with factory() as db:
                repo = PersonaRepository(db)
                service = OnboardingService(repo)
                service.extraction = agent
                responses.append(await service.build_draft(await repo.get_session("session-1")))
                await db.commit()
        return responses, agent.calls

    return asyncio.run(with_db(scenario))


def test_build_finishes_with_rule_based_draft_when_llm_is_down():
    (draft,), _ = _build(DOWN)

    assert draft.source == "fallback"
    assert draft.is_confirmed is False
    assert draft.scores["seriousness"] == 80  # 선택지 "진지하게 만날 사람"
    assert draft.scores["avoidance"] == 50  # 근거를 못 읽었으니 기본값
    assert draft.confidence["avoidance"] == "LOW"
    assert draft.narrative is None


def test_next_build_retries_llm_and_replaces_fallback_draft():
    (fallback, rebuilt), calls = _build(DOWN, GOOD, builds=2)

    assert calls == 2
    assert fallback.source == "fallback"
    assert rebuilt.source == "llm"
    assert rebuilt.version == 2
    assert rebuilt.scores["avoidance"] == 78
    assert rebuilt.interests == ["러닝"]


def test_build_persists_valid_summaries_and_drops_invalid_or_duplicate_categories():
    raw = RawExtraction(
        avoidance=78,
        seriousness=90,
        summaries=[
            Summary(category="intimacy", title="천천히 가까워지는 편", content="서서히 알아가는 걸 편하게 느껴요"),
            Summary(category="intimacy", title="중복", content="같은 area 두 번째 — 버려져야 함"),
            Summary(category="not_a_real_area", title="가짜", content="area 밖 값 — 버려져야 함"),
            Summary(category="orientation", title="진지하게 만나는 편", content="오래 볼 사람을 찾는 중이에요"),
        ],
    )

    (draft,), _ = _build(raw)

    assert [s.category for s in draft.summaries] == ["intimacy", "orientation"]
    assert draft.summaries[0].title == "천천히 가까워지는 편"


def test_build_summaries_default_to_empty_list_when_llm_omits_them():
    (draft,), _ = _build(GOOD)

    assert draft.summaries == []


def test_valid_summaries_keeps_first_occurrence_per_known_area():
    summaries = [
        Summary(category="conflict", title="a", content="a"),
        Summary(category="unknown", title="b", content="b"),
        Summary(category="conflict", title="c", content="c"),
        Summary(category="ideal", title="d", content="d"),
    ]

    out = valid_summaries(summaries)

    assert out == [
        {"category": "conflict", "title": "a", "content": "a"},
        {"category": "ideal", "title": "d", "content": "d"},
    ]


def test_retry_while_llm_still_down_returns_same_fallback_draft():
    (first, second), calls = _build(DOWN, DOWN, builds=2)

    assert calls == 2  # 다시 시도는 했다
    assert second.source == "fallback"
    assert second.persona_id == first.persona_id  # 같은 폴백 초안을 새 버전으로 쌓지 않는다
    assert second.version == 1


@pytest.mark.parametrize(
    ("answer", "score", "confidence"),
    [
        ("진지하게 만날 사람", 80, "MEDIUM"),
        ("편하게 알아가기", 25, "MEDIUM"),
        ("아직 잘 모르겠어요", 50, "LOW"),  # 방향이 없는 답은 근거로 치지 않는다
    ],
)
def test_fallback_scores_seriousness_from_orientation_choice(answer, score, confidence):
    (draft,), _ = _build(DOWN, orientation_answer=answer)

    assert draft.scores["seriousness"] == score
    assert draft.confidence["seriousness"] == confidence


def test_supplement_rebuild_never_overwrites_llm_draft_with_fallback():
    async def scenario(factory):
        async with factory() as db:
            db.add(_session())
            await db.commit()
        agent = FakeExtraction(GOOD, DOWN)
        async with factory() as db:
            repo = PersonaRepository(db)
            service = OnboardingService(repo)
            service.extraction = agent
            await service.build_draft(await repo.get_session("session-1"))
            await db.commit()
        async with factory() as db:
            repo = PersonaRepository(db)
            service = OnboardingService(repo)
            service.extraction = agent
            with pytest.raises(BuildFailed):  # api 가 롤백하고 503 — 기존 LLM 초안은 그대로
                await service.supplement(await repo.get_session("session-1"), "avoidance", "혼자 시간이 꼭 필요해요")
            await db.rollback()
        async with factory() as db:
            return await PersonaRepository(db).latest_persona("session-1")

    latest = asyncio.run(with_db(scenario))

    assert (latest.version, latest.source) == (1, "llm")
    assert latest.scores["avoidance"] == 78


def test_build_api_returns_fallback_draft_as_200_with_source():
    calls = []

    class FakeService:
        async def get_session_for_update(self, session_id):
            return SimpleNamespace(id=session_id)

        async def build_draft(self, session):
            return PersonaResponse(persona_id="p1", source="fallback", scores={"seriousness": 80})

    service = FakeService()
    service.repo = service

    class FakeDb:
        async def commit(self):
            calls.append("commit")

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_service] = lambda: service
    app.dependency_overrides[get_db] = lambda: FakeDb()

    res = TestClient(app).post("/v1/persona/session-1/build")

    assert res.status_code == 200
    assert res.json()["source"] == "fallback"
    assert calls == ["commit"]
