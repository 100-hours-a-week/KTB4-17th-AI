"""GET /v1/persona/users/{user_id} — 마이페이지가 그 유저의 확정 페르소나를 꺼내는 입구."""

import asyncio

import httpx
import pytest
from conftest import seed_persona, with_db
from fastapi import FastAPI

from app.core.config import get_settings
from app.core.db import get_db
from app.features.persona import api

KEY = "test-internal-key"


@pytest.fixture(autouse=True)
def _internal_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_api_key", KEY)


def _get(user_id, *, seeds=(), headers=None):
    """seeds 로 페르소나를 저장해 두고 user_id 로 조회한 응답."""

    async def scenario(factory):
        for seed in seeds:
            seed = dict(seed)
            columns = seed.pop("columns", {})  # seed_persona 가 받지 않는 칸은 저장 뒤에 채운다
            async with factory() as db:
                record = await seed_persona(db, **seed)
                for name, value in columns.items():
                    setattr(record, name, value)
                await db.commit()

        async def db_dep():
            async with factory() as db:
                yield db

        app = FastAPI()
        app.include_router(api.router)
        app.dependency_overrides[get_db] = db_dep
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(
                f"/v1/persona/users/{user_id}",
                headers={"X-Internal-Api-Key": KEY} if headers is None else headers,
            )

    return asyncio.run(with_db(scenario))


def test_returns_confirmed_persona_of_that_user():
    res = _get(
        "1001",
        seeds=[
            dict(persona_id="p1", user_id="1001", nickname="민수", scores={"avoidance": 78}, mbti="INTP"),
            dict(persona_id="p2", user_id="2002", nickname="지은", scores={"avoidance": 20}),
        ],
    )

    assert res.status_code == 200
    body = res.json()
    assert body["user_id"] == "1001"
    assert body["persona_id"] == "p1"
    assert body["nickname"] == "민수"
    assert body["mbti"] == "INTP"
    # 화면에 없는 내부 값은 내보내지 않는다
    for hidden in ("scores", "confidence", "accuracy", "gaps", "changes", "is_confirmed"):
        assert hidden not in body


NARRATIVE = {
    "headline": "천천히, 그러나 깊게 다가가는 사람",
    "body": "처음에는 상대를 조심스럽게 살피지만, 신뢰가 쌓이면 솔직하게 마음을 표현해요.",
    "traits": ["신중함", "다정함", "꾸준함", "호기심"],
}
CARDS = [
    {"category": "intimacy", "title": "각자의 시간도 소중해요", "content": "거리가 지켜질 때 더 편하게 다가가요."},
    {"category": "conflict", "title": "이유부터 들어봐요", "content": "둘 다 괜찮은 방법을 찾아요."},
]
STYLE = {
    "summary": "짧고 다정한 문장으로 리액션을 자주 해요.",
    "frequent_phrases": ["오 진짜요?", "완전 좋다"],
    "endings": ["~요ㅎㅎ", "~네용"],
    "message_length": "짧게 여러 번 보내요",
    "laughter": "ㅎㅎ, ㅋㅋ를 섞어 써요",
    "question_rate": "꼬리 질문을 자주 해요",
    "slang": ["ㄹㅇ"],
    "phrase_weights": {"~네용": 0.8},
}
TEXTS = {
    "interests": ["필름 카메라 산책", "보드게임"],
    "routine": ["퇴근 후 30분 러닝"],
    "date_prefer": ["조용한 전시 관람"],
    "date_avoid": ["너무 시끄러운 술집"],
}


def test_returns_screen_fields():
    seed = dict(
        persona_id="p1",
        user_id="1001",
        nickname="민수",
        texts=TEXTS,
        columns={"narrative": NARRATIVE, "summaries": CARDS, "conversation_style": STYLE},
    )
    res = _get("1001", seeds=[seed])

    assert res.status_code == 200
    body = res.json()
    assert body["narrative"] == NARRATIVE
    assert body["summaries"] == CARDS
    for key, value in TEXTS.items():
        assert body[key] == value
    # 말투는 화면 칸만. 내부 가중치는 빠진다
    assert body["conversation_style"] == {k: v for k, v in STYLE.items() if k != "phrase_weights"}


def test_style_is_null_before_extraction():
    res = _get("1001", seeds=[dict(persona_id="p1", user_id="1001", nickname="민수")])

    assert res.json()["conversation_style"] is None


def test_draft_only_user_is_not_found():
    """/confirm 전 초안은 마이페이지에 보여 주지 않는다."""
    res = _get("1001", seeds=[dict(persona_id="p1", user_id="1001", nickname="민수", confirmed=False)])

    assert res.status_code == 404
    assert res.json() == {"detail": "persona not found"}


def test_unknown_user_is_not_found():
    res = _get("9999")

    assert res.status_code == 404


def test_missing_internal_key_is_rejected():
    res = _get("1001", seeds=[dict(persona_id="p1", user_id="1001", nickname="민수")], headers={})

    assert res.status_code == 401


def test_accepts_x_api_key_header():
    res = _get("1001", seeds=[dict(persona_id="p1", user_id="1001", nickname="민수")], headers={"X-API-Key": KEY})

    assert res.status_code == 200


def test_too_long_user_id_is_rejected():
    res = _get("x" * 65)

    assert res.status_code == 422
