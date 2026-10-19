"""소재 매칭 — 유사도 행렬 → 소재 선택, 중복 제거, 카테고리·상태·모델 필터, 대화 계획."""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import get_settings
from app.v2.persona.models import UserPersonaFact
from app.v2.simulation.schemas import TopicItem
from app.v2.simulation.topics import build_segments, fetch_facts, match_topics
from tests.conftest import with_db
from tests.v2.sim_fixtures import fact, vec

# 축: 0 = 산/등산, 1 = 책, 2 = 요리, 3 = 음악
HIKE_A = vec(1.0, 0.0)
HIKE_B = vec(0.95, 0.1)  # HIKE_A 와 매우 비슷 (≈0.99)
HIKE_B2 = vec(0.9, 0.2)  # 산 소재지만 HIKE_B 와도 비슷 → 중복
BOOK = vec(0.0, 1.0)
COOK_A = vec(0.0, 0.0, 1.0)
COOK_B = vec(0.0, 0.1, 1.0)
MUSIC = vec(0.0, 0.0, 0.0, 1.0)


def test_shared_topics_greedy_by_similarity():
    fa = [fact(1, "주말마다 산에 간다", HIKE_A), fact(2, "요리를 즐긴다", COOK_A, age_days=1)]
    fb = [fact(11, "트레킹 동호회 활동", HIKE_B), fact(12, "집밥 만들기", COOK_B, age_days=1)]
    plan = match_topics(fa, fb, turns=10, tau_shared=0.6)
    assert plan.mode == "shared"
    assert [it.kind for it in plan.items] == ["shared", "shared"]
    first, second = plan.items
    # 유사도 높은 쌍(요리 ≈0.995)이 먼저, 그다음 산(≈0.99)
    assert (first.a_fact.fact_id, first.b_fact.fact_id) == (2, 12)
    assert (second.a_fact.fact_id, second.b_fact.fact_id) == (1, 11)
    assert first.sim >= second.sim > 0.95
    # 소재 fact 가 노출 목록 앞에 오고 ref 는 그 순서대로
    assert first.a_fact.ref == "A-f1" and second.a_fact.ref == "A-f2"


def test_dedup_skips_near_duplicate_topic():
    fa = [fact(1, "등산", HIKE_A), fact(2, "산책", HIKE_B2, age_days=1)]
    fb = [fact(11, "트레킹", HIKE_B), fact(12, "둘레길", HIKE_A, age_days=1)]
    plan = match_topics(fa, fb, turns=10, tau_shared=0.6, dedup_sim=0.8)
    shared = [it for it in plan.items if it.kind == "shared"]
    assert len(shared) == 1  # 두 번째 산 소재는 첫 소재와 너무 비슷해서 건너뜀


def test_same_fact_not_reused_across_topics():
    fa = [fact(1, "등산", HIKE_A)]
    fb = [fact(11, "트레킹", HIKE_B), fact(12, "등산로", HIKE_B2)]
    plan = match_topics(fa, fb, turns=10, tau_shared=0.6, dedup_sim=1.01)
    assert len([it for it in plan.items if it.kind == "shared"]) == 1


def test_max_shared_cap():
    fa = [fact(1, "등산", HIKE_A), fact(2, "책", BOOK), fact(3, "요리", COOK_A), fact(4, "음악", MUSIC)]
    fb = [fact(11, "트레킹", HIKE_B), fact(12, "독서", BOOK), fact(13, "요리", COOK_B), fact(14, "공연", MUSIC)]
    plan = match_topics(fa, fb, turns=10, tau_shared=0.6, max_shared=3)
    assert len(plan.items) == 3


def test_solo_mode_when_no_shared_uses_latest_hobby():
    fa = [fact(1, "새 취미 등산", HIKE_A), fact(2, "옛 취미 요리", COOK_A, age_days=5)]
    fb = [fact(11, "독서", BOOK, category="일상"), fact(12, "음악 감상", MUSIC)]
    plan = match_topics(fa, fb, turns=10, tau_shared=0.9)
    assert plan.mode == "solo"
    assert [(it.kind, it.a_fact and it.a_fact.fact_id, it.b_fact and it.b_fact.fact_id) for it in plan.items] == [
        ("solo", 1, None),
        ("solo", None, 12),  # 일상 fact 는 단방향 소재 후보가 아니다
    ]


def test_one_shared_adds_solo_hobbies_without_reusing_fact():
    fa = [fact(1, "등산", HIKE_A), fact(2, "요리", COOK_A, age_days=1)]
    fb = [fact(11, "트레킹", HIKE_B), fact(12, "음악", MUSIC, age_days=1)]
    plan = match_topics(fa, fb, turns=10, tau_shared=0.9)
    kinds = [it.kind for it in plan.items]
    assert kinds == ["shared", "solo", "solo"]
    assert plan.items[1].a_fact.fact_id == 2  # 공통 소재로 쓴 1번은 다시 쓰지 않는다
    assert plan.items[2].b_fact.fact_id == 12


def test_persona_only_without_facts():
    plan = match_topics([], [], turns=10)
    assert plan.mode == "persona_only"
    assert plan.items == [] and plan.facts_a == [] and plan.facts_b == []
    assert plan.segments[-1].label == "closing"


def test_partner_without_facts_gives_solo():
    plan = match_topics([fact(1, "등산", HIKE_A)], [], turns=10)
    assert plan.mode == "solo"
    assert plan.facts_b == []


def test_exposed_facts_capped_but_topic_facts_first():
    fa = [fact(i, f"취미{i}", vec(*([0.0] * (i % 6)), 1.0), age_days=i) for i in range(1, 12)]
    fb = [fact(100, "등산 비슷", fa[5].embedding)]
    plan = match_topics(fa, fb, turns=10, tau_shared=0.99, max_facts=3)
    assert len(plan.facts_a) == 3
    topic_fact_ids = {it.a_fact.fact_id for it in plan.items if it.a_fact}
    assert topic_fact_ids <= {f.fact_id for f in plan.facts_a}
    assert [f.ref for f in plan.facts_a] == ["A-f1", "A-f2", "A-f3"]


@pytest.mark.parametrize("turns", range(1, 16))
def test_segments_cover_all_rounds(turns):
    items = [TopicItem(id="t1", kind="shared"), TopicItem(id="t2", kind="shared"), TopicItem(id="t3", kind="solo")]
    segs = build_segments(turns, items)
    covered = [r for s in segs for r in range(s.start_round, s.end_round + 1)]
    assert covered == list(range(1, turns + 1))
    assert segs[-1].label == "closing" and segs[-1].start_round == turns


def test_segments_ten_rounds_match_plan():
    items = [TopicItem(id="t1", kind="shared"), TopicItem(id="t2", kind="shared"), TopicItem(id="t3", kind="solo")]
    segs = [(s.start_round, s.end_round, s.label, s.topic_id) for s in build_segments(10, items)]
    assert segs == [
        (1, 2, "greeting", None),
        (3, 5, "topic", "t1"),
        (6, 8, "topic", "t2"),
        (9, 9, "topic", "t3"),
        (10, 10, "closing", None),
    ]


def test_segments_without_topics_use_style_and_date():
    segs = [(s.start_round, s.end_round, s.label) for s in build_segments(10, [])]
    assert segs == [(1, 2, "greeting"), (3, 8, "style"), (9, 9, "date"), (10, 10, "closing")]


def test_fetch_facts_filters_status_category_model_and_sanitizes():
    model = get_settings().embedding_model_name
    e = [0.1] * 1024

    async def scenario(sessions):
        async with sessions() as db:
            db.add_all(
                [
                    UserPersonaFact(
                        user_id="u",
                        category="취미",
                        fact="# 주말\n등산",
                        status="confirmed",
                        embedding=e,
                        embedding_model=model,
                    ),
                    UserPersonaFact(
                        user_id="u",
                        category="연애관",
                        fact="연애관",
                        status="confirmed",
                        embedding=e,
                        embedding_model=model,
                    ),
                    UserPersonaFact(
                        user_id="u",
                        category="취미",
                        fact="잠정",
                        status="tentative",
                        embedding=e,
                        embedding_model=model,
                    ),
                    UserPersonaFact(
                        user_id="u",
                        category="일상",
                        fact="다른모델",
                        status="confirmed",
                        embedding=e,
                        embedding_model="old-model",
                    ),
                    UserPersonaFact(
                        user_id="u",
                        category="일상",
                        fact="벡터없음",
                        status="confirmed",
                        embedding=None,
                        embedding_model=model,
                    ),
                    UserPersonaFact(
                        user_id="other",
                        category="취미",
                        fact="남의것",
                        status="confirmed",
                        embedding=e,
                        embedding_model=model,
                    ),
                ]
            )
            await db.commit()
            return await fetch_facts(db, "u"), await fetch_facts(db, None)

    rows, none_rows = asyncio.run(with_db(scenario))
    assert [r.text for r in rows] == ["주말 등산"]
    assert none_rows == []


def test_solo_uses_non_hobby_fact_when_no_hobby():
    """Grok 반례: 가치관 fact 만 있으면 persona_only 로 버려졌다."""
    plan = match_topics([fact(1, "약속을 중요하게 생각한다", BOOK, category="가치관")], [], turns=10)
    assert plan.mode == "solo" and plan.facts_a[0].fact_id == 1
