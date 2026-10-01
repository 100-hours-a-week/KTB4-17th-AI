"""/build 폴백 — 추출 LLM 이 실패해도 온보딩은 끝나야 한다.

LLM 없이 규칙으로 만든 초안(source="fallback")을 돌려주고, 다음 /build 때 LLM 추출을 다시 시도한다.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from conftest import with_db
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.features.persona import api
from app.features.persona.agents import BuildFailed, ExtractionAgent, TaggingAgent
from app.features.persona.models import OnboardingSession, OnboardingTurn
from app.features.persona.repository import PersonaRepository
from app.features.persona.schemas import Narrative, PersonaResponse, RawExtraction, Summary, Tags
from app.features.persona.service import OnboardingService, valid_summaries


class FakeExtraction(ExtractionAgent):
    """추출 LLM 자리. results 를 차례로 쓴다 — 예외면 raise, 아니면 그 추출 결과."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    async def extract(self, history, *, answered=None, trace_metadata=None):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeTagging(TaggingAgent):
    """태깅 LLM 자리. results 를 차례로 쓴다 — None 이면 태깅 실패."""

    def __init__(self, *results):
        self.results = list(results)
        self.seen = []
        self.timeouts = []

    async def tag(self, question, answer, *, timeout=None, trace_metadata=None):
        self.seen.append(answer)
        self.timeouts.append(timeout)
        return self.results.pop(0)


DOWN = BuildFailed("LLM call failed: timeout")
GOOD = RawExtraction(avoidance=78, seriousness=90, interests=["러닝"])


def _tags(*primary):
    return {"primary": list(primary), "secondary": [], "off_topic": False}


def _session(orientation_answer="진지하게 만날 사람", mbti=None, weekend=None) -> OnboardingSession:
    """10턴을 다 마친 온보딩. 답한 건 관심사·각자 생활·관계 진지도, 주말 질문은 기본으로 건너뜀.

    weekend 를 주면 주말 질문에 그 턴 필드(answer·tags)로 답한 것으로 만든다."""
    weekend_turn = {"answer": None, "skipped": True, **(weekend or {})}
    if weekend:
        weekend_turn["skipped"] = False
    turns = [
        OnboardingTurn(
            turn_index=0,
            topic_id="interests",
            question="요즘 뭐 하면서 지내요?",
            answer="러닝해요",
            tags=_tags("interests"),
        ),
        OnboardingTurn(turn_index=1, topic_id="weekend", question="주말엔 뭐 해요?", **weekend_turn),
        OnboardingTurn(
            turn_index=2,
            topic_id="share_vs_separate",
            question="각자 생활?",
            answer="각자 시간이 있어야 해요",
            tags=_tags("avoidance"),
        ),
        OnboardingTurn(
            turn_index=9,
            topic_id="orientation",
            question="어떤 연애?",
            answer=orientation_answer,
            tags=_tags("seriousness"),
        ),
    ]
    return OnboardingSession(
        id="session-1",
        user_id="user-1",
        nickname="민수",
        mbti=mbti,
        total_turns=10,
        turn_index=10,
        pending_topic_id=None,
        used_topic_ids=["interests", "weekend", "share_vs_separate", "orientation"],
        coverage={"primary": {"interests": 1, "avoidance": 1, "seriousness": 1}, "secondary": {}},
        status="completed",
        turns=turns,
    )


def _build(
    *extraction_results,
    orientation_answer="진지하게 만날 사람",
    builds=1,
    mbti=None,
    agent=None,
    weekend=None,
    tagger=None,
):
    """/build 를 builds 번 부른다. 요청마다 새 DB 세션."""

    async def scenario(factory):
        async with factory() as db:
            db.add(_session(orientation_answer, mbti, weekend))
            await db.commit()
        extractor = agent or FakeExtraction(*extraction_results)
        responses = []
        for _ in range(builds):
            async with factory() as db:
                repo = PersonaRepository(db)
                service = OnboardingService(repo)
                service.extraction = extractor
                if tagger is not None:
                    service.tagging = tagger
                responses.append(await service.build_draft(await repo.get_session("session-1")))
                await db.commit()
        return responses, getattr(extractor, "calls", None)

    return asyncio.run(with_db(scenario))


def test_build_finishes_with_rule_based_draft_when_llm_is_down():
    (draft,), _ = _build(DOWN)

    assert draft.source == "fallback"
    assert draft.is_confirmed is False
    assert draft.scores["seriousness"] == 80  # 선택지 "진지하게 만날 사람"
    assert draft.scores["avoidance"] is None  # 근거를 못 읽었으니 모름(null)
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


# ── 직접 답한 차원만 ───────────────────────────────────


def test_build_drops_scores_for_skipped_or_unasked_questions():
    """LLM 이 건너뛴 주말 질문(date_prefer)·묻지 않은 차원(anxiety·problem_solving)까지 추측해 채워도 버린다."""
    raw = RawExtraction(
        avoidance=78,
        seriousness=90,
        anxiety=20,  # 묻지 않음
        problem_solving=85,  # 묻지 않음
        interests=["러닝"],
        routine=["늦잠"],  # 건너뛴 주말 질문
        date_prefer=["각자 시간 챙기기"],  # 건너뛴 주말 질문
    )

    (draft,), _ = _build(raw)

    assert (draft.scores["avoidance"], draft.scores["seriousness"]) == (78, 90)
    assert (draft.scores["anxiety"], draft.confidence["anxiety"]) == (None, "LOW")
    assert (draft.scores["problem_solving"], draft.confidence["problem_solving"]) == (None, "LOW")
    assert draft.interests == ["러닝"]
    assert (draft.routine, draft.date_prefer) == ([], [])


def test_build_keeps_only_what_the_answer_itself_revealed():
    """주말 질문은 일상·선호 데이트·피하는 것을 겨누지만, 답에 일상만 있으면 일상만 남는다."""
    raw = RawExtraction(routine=["집에서 쉬기"], date_prefer=["각자 시간 챙기기"], date_avoid=["만나자마자 깊어지기"])

    (draft,), _ = _build(raw, weekend={"answer": "그냥 집에서 쉬어요", "tags": _tags("routine")})

    assert draft.routine == ["집에서 쉬기"]
    assert (draft.date_prefer, draft.date_avoid) == ([], [])


def test_answer_whose_tagging_failed_is_tagged_again_at_build():
    """온보딩 중 태깅이 실패한 답(tags 없음)은 /build 때 다시 태깅해 그 답의 근거만 인정한다."""
    raw = RawExtraction(routine=["집에서 쉬기"], date_prefer=["각자 시간 챙기기"])
    tagger = FakeTagging(Tags(primary=["routine"]))

    (draft,), _ = _build(raw, weekend={"answer": "그냥 집에서 쉬어요", "tags": None}, tagger=tagger)

    assert tagger.seen == ["그냥 집에서 쉬어요"]
    assert (draft.routine, draft.date_prefer) == (["집에서 쉬기"], [])


def test_retagging_at_build_waits_longer_than_onboarding_tagging():
    """/build 는 사용자가 결과를 기다리는 단계라 온보딩 중(답마다 바로 다음 질문)보다 넉넉히 기다린다 (#80)."""
    from app.core.config import get_settings

    tagger = FakeTagging(Tags(primary=["routine"]))

    _build(RawExtraction(routine=["집에서 쉬기"]), weekend={"answer": "그냥 쉬어요", "tags": None}, tagger=tagger)

    settings = get_settings()
    assert tagger.timeouts == [settings.persona_retag_timeout_s]
    assert settings.persona_retag_timeout_s > settings.onboarding_tag_timeout_s


def test_answer_that_cannot_be_tagged_even_at_build_counts_for_nothing():
    """다시 태깅해도 실패하면 그 답에서 무엇이 드러났는지 확인할 수 없다 — 아무 차원도 인정하지 않는다."""
    raw = RawExtraction(routine=["집에서 쉬기"], date_prefer=["각자 시간 챙기기"])

    (draft,), _ = _build(raw, weekend={"answer": "그냥 집에서 쉬어요", "tags": None}, tagger=FakeTagging(None))

    assert (draft.routine, draft.date_prefer) == ([], [])


def test_build_drops_summary_cards_for_areas_without_answers():
    raw = RawExtraction(
        avoidance=78,
        problem_solving=85,  # 묻지 않음 → conflict 카드도 추측
        summaries=[
            Summary(category="intimacy", title="각자 시간 챙기는 편", content="혼자 시간이 필요해요"),
            Summary(category="conflict", title="중간 지점 찾는 편", content="묻지도 않은 갈등 얘기"),
        ],
    )

    (draft,), _ = _build(raw)

    assert [s.category for s in draft.summaries] == ["intimacy"]


def test_build_asks_llm_only_about_answered_items(monkeypatch):
    """실제 추출 에이전트를 통과시키고 LLM 호출만 가짜로 — LLM 에 '직접 답한 항목'만 알려준다."""
    from app.features.persona import agents

    sent = {}

    async def fake_call(**kwargs):
        sent["prompt"] = kwargs["messages"][-1]["content"]
        return json.dumps({"avoidance": 78, "anxiety": 20})

    monkeypatch.setattr(agents, "_call", fake_call)

    (draft,), _ = _build(agent=ExtractionAgent())

    assert "avoidance (거리 두기)" in sent["prompt"]
    assert "interests (관심사)" in sent["prompt"]
    assert "anxiety (관계 불안)" not in sent["prompt"]  # 묻지 않음
    assert "date_prefer (선호 데이트)" not in sent["prompt"]  # 건너뛴 주말 질문
    assert (draft.scores["avoidance"], draft.scores["anxiety"]) == (78, None)


# ── MBTI ───────────────────────────────────────────────


def test_draft_shows_mbti_given_at_start_before_confirm():
    (draft,), _ = _build(GOOD, mbti="ENFP")

    assert draft.is_confirmed is False
    assert draft.mbti == "ENFP"


def test_mbti_does_not_change_the_onboarding_result():
    """온보딩 결과는 답변으로만 — MBTI 가 있어도 답하지 않은 차원은 근거 없음(50·LOW) 그대로."""
    (with_mbti,), _ = _build(GOOD, mbti="ENFP")
    (without,), _ = _build(GOOD)

    assert with_mbti.scores == without.scores
    assert with_mbti.confidence == without.confidence
    assert "mbti_estimates" not in with_mbti.model_dump()


def test_answer_confirmed_only_by_retagging_is_not_low_confidence():
    """온보딩 중 태깅이 실패해 커버리지에 안 잡힌 답도, /build 때 다시 태깅해 확인되면 근거로 친다."""
    raw = RawExtraction(**GOOD.model_dump(exclude_unset=True), contact_rhythm=20)
    tagger = FakeTagging(Tags(primary=["contact_rhythm"]))

    (draft,), _ = _build(raw, weekend={"answer": "주말엔 톡 거의 안 해요", "tags": None}, tagger=tagger)

    assert draft.scores["contact_rhythm"] == 20
    assert draft.confidence["contact_rhythm"] != "LOW"


# ── 서술의 추측성 문장 ─────────────────────────────────


def _with_narrative(body, traits=(), headline="각자의 시간을 존중하는 편"):
    return RawExtraction(
        **GOOD.model_dump(exclude_unset=True),
        narrative=Narrative(headline=headline, body=body, traits=list(traits)),
    )


def test_speculative_sentence_is_removed_from_narrative_body():
    """사용자가 하지 않은 말을 짐작한 문장("~부담스러우실 수 있겠네요")은 빼고, 한 말을 옮긴 문장은 남긴다.
    짐작 문장이 시뮬레이션 대본에서 '상대가 한 말'로 새어 나왔다 (#67)."""
    raw = _with_narrative(
        "각자의 시간을 챙기는 걸 좋아하는 편이에요. 너무 캐묻는 것은 부담스러우실 수 있겠네요. 요즘은 러닝을 즐겨요."
    )

    (draft,), _ = _build(raw)

    assert draft.narrative.body == "각자의 시간을 챙기는 걸 좋아하는 편이에요. 요즘은 러닝을 즐겨요."


def test_sentence_carrying_the_users_wish_is_kept():
    """'~면 좋겠어요'는 사용자의 바람을 옮긴 문장이지 짐작이 아니다."""
    raw = _with_narrative("천천히 알아가면 좋겠어요. 요즘은 러닝을 즐겨요.")

    (draft,), _ = _build(raw)

    assert draft.narrative.body == "천천히 알아가면 좋겠어요. 요즘은 러닝을 즐겨요."


def test_speculative_trait_is_removed():
    raw = _with_narrative(
        "각자의 시간을 챙기는 편이에요.",
        traits=["각자 시간을 중요하게 생각함", "연락이 뜸하면 서운해할 것 같아요"],
    )

    (draft,), _ = _build(raw)

    assert draft.narrative.traits == ["각자 시간을 중요하게 생각함"]


@pytest.mark.parametrize(
    ("headline", "body"),
    [
        ("진지한 만남을 원할 것 같아요", "각자의 시간을 챙기는 편이에요."),  # 한 줄 요약이 짐작
        ("각자의 시간을 존중하는 편", "연락이 뜸하면 서운하실 수도 있겠어요."),  # 본문이 전부 짐작
    ],
    ids=["headline", "whole-body"],
)
def test_narrative_is_dropped_when_nothing_grounded_is_left(headline, body):
    """한 줄 요약이 짐작이거나 본문이 전부 짐작이면 서술을 버린다 — 점수와 모순될 때처럼."""
    (draft,), _ = _build(_with_narrative(body, headline=headline))

    assert draft.narrative is None


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
        ("아직 잘 모르겠어요", None, "LOW"),  # 방향이 없는 답은 근거로 치지 않는다
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
