"""load_persona — simulation·practice 가 저장된 페르소나를 꺼내는 입구."""

import asyncio

from conftest import seed_persona, with_db

from app.features.persona.lookup import load_persona
from app.features.persona.schemas import PersonaRef


def _load(**seed):
    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="p1", user_id="u1", nickname="민수", **seed)
        async with factory() as db:
            return await load_persona(db, PersonaRef(persona_id="p1"))

    return asyncio.run(with_db(scenario))


def test_confirmed_persona_carries_its_mbti():
    loaded = _load(mbti="INTP", session_mbti="INTP")

    assert loaded.response.mbti == "INTP"


def test_old_persona_confirmed_without_mbti_uses_mbti_given_at_start():
    """MBTI 를 확정 행에 옮겨 적기 전에 확정된 옛 페르소나 — 시작 때 받은 값으로 채운다."""
    loaded = _load(mbti=None, session_mbti="ENFP")

    assert loaded.response.mbti == "ENFP"


def test_persona_without_any_mbti_loads_with_none():
    loaded = _load()

    assert loaded.response.mbti is None


def test_mbti_does_not_change_scores_used_for_simulation_and_practice():
    """궁합 점수·연기에 쓰는 점수는 답변에서 나온 그대로 — MBTI 는 말투 힌트로만 간다(profile.describe)."""
    loaded = _load(mbti="ISTP", scores={"avoidance": 80})

    assert loaded.response.scores == {"avoidance": 80}
    assert loaded.response.mbti == "ISTP"


def test_old_persona_default_50_without_evidence_is_read_as_unknown():
    """null 도입 전 행은 근거 없는 차원이 '신뢰도 LOW 인 50'으로 저장돼 있다 — 모름(null)으로 읽는다.
    근거 있는 50(MEDIUM 이상)은 진짜 중간값이라 그대로."""
    loaded = _load(
        scores={"avoidance": 80, "anxiety": 50, "disclosure": 50},
        confidence={"avoidance": "HIGH", "anxiety": "LOW", "disclosure": "MEDIUM"},
    )

    assert loaded.response.scores == {"avoidance": 80, "anxiety": None, "disclosure": 50}
