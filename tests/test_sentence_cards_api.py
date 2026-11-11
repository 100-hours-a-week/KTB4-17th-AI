"""문장 판정 카드 API — in-memory SQLite 에 진짜 service · repository 를 붙여 HTTP 계약을 확인한다.

계약 문서: 문장_판정_카드_BE_연동_API.md · sentence_cards.schema.json
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.db import Base, get_db
from app.features.persona.schemas import SCORED
from app.features.sentence_cards import api
from app.features.sentence_cards.models import Sentence, SentenceCard
from app.features.sentence_cards.service import DAILY_LIMIT, SentenceCardService

T0 = datetime(2026, 11, 11, 3, 0, tzinfo=UTC)  # KST 12:00


class Env:
    def __init__(self, client: TestClient, factory, clock: list[datetime]) -> None:
        self.client = client
        self.factory = factory
        self.clock = clock

    def seed(self, *rows: dict) -> list[str]:
        async def insert() -> list[str]:
            async with self.factory() as db:
                objs = [Sentence(**{"family_id": "f", "source": "team_written", **row}) for row in rows]
                db.add_all(objs)
                await db.commit()
                return [o.id for o in objs]

        return self.client.portal.call(insert)

    def cards(self) -> list[SentenceCard]:
        async def fetch() -> list[SentenceCard]:
            async with self.factory() as db:
                return list((await db.execute(select(SentenceCard).order_by(SentenceCard.shown_at))).scalars())

        return self.client.portal.call(fetch)

    def next(self, user_id: str = "1001", request_id: str | None = None):
        return self.client.post(
            "/v1/sentence-cards/next", json={"request_id": request_id or str(uuid.uuid4()), "user_id": user_id}
        )

    def batch(self, answers: list[dict], batch_id: str | None = None):
        return self.client.post(
            "/v1/sentence-cards/answers/batch", json={"batch_id": batch_id or str(uuid.uuid4()), "answers": answers}
        )


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "")
    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    clock = [T0]

    async def real_db():
        async with factory() as db:
            yield db

    def service(db=Depends(get_db)) -> SentenceCardService:
        return SentenceCardService(db, now=lambda: clock[0], rng=random.Random(0))

    async def create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = real_db
    app.dependency_overrides[api.get_service] = service
    with TestClient(app) as client:
        client.portal.call(create_tables)
        yield Env(client, factory, clock)
    get_settings.cache_clear()


def direction(sentence: str = "주말엔 거의 집에서 혼자 쉬어요", **kw) -> dict:
    return {"sentence": sentence, "question_type": "DIRECTION", "dimension": "avoidance", **kw}


def axis(sentence: str = "답장은 생각날 때 몰아서 하는 편이에요", **kw) -> dict:
    return {"sentence": sentence, "question_type": "AXIS", "dimension": "contact_rhythm", **kw}


def answer(card_id: str, choice_id: str, *, user_id: str = "1001", at: datetime = T0) -> dict:
    return {"card_id": card_id, "user_id": user_id, "choice_id": choice_id, "answered_at": at.isoformat()}


# ── next ────────────────────────────────────────────────


def test_next_returns_direction_card_and_same_request_id_returns_same_card(env):
    env.seed(direction())
    rid = str(uuid.uuid4())
    first = env.next(request_id=rid)
    again = env.next(request_id=rid)

    assert first.status_code == 200
    body = first.json()
    assert body["sentence"] == "주말엔 거의 집에서 혼자 쉬어요"
    assert body["question"] == "이런 말을 하는 사람은 어느 쪽일까요?"
    ids = [c["choice_id"] for c in body["choices"]]
    assert sorted(ids[:2]) == ["high", "low"] and ids[2] == "unsure"
    labels = {c["choice_id"]: c["label"] for c in body["choices"]}
    assert labels["high"] == SCORED["avoidance"].high and labels["low"] == SCORED["avoidance"].low
    assert datetime.fromisoformat(body["expires_at"]) == T0 + timedelta(hours=24)
    assert set(body) == {"card_id", "sentence", "question", "choices", "expires_at"}  # 답안지는 내려가지 않는다
    assert again.status_code == 200 and again.json() == body
    assert len(env.cards()) == 1


def test_axis_card_keeps_degree_order_and_names_the_dimension(env):
    env.seed(axis())
    body = env.next().json()
    assert body["question"] == f"이 말은 '{SCORED['contact_rhythm'].label}'에 대한 이야기인가요?"
    assert [c["choice_id"] for c in body["choices"]] == ["direct", "inferred", "none", "unsure"]


def test_next_never_repeats_a_sentence_for_the_same_user_then_204(env):
    env.seed(direction("문장 하나"), direction("문장 둘"))
    got = {env.next().json()["sentence"], env.next().json()["sentence"]}
    assert got == {"문장 하나", "문장 둘"}
    assert env.next().status_code == 204


def test_next_spreads_exposure_to_the_least_shown_sentence(env):
    env.seed(direction("문장 하나"), direction("문장 둘"))
    first = env.next(user_id="A").json()["sentence"]
    second = env.next(user_id="B").json()["sentence"]
    assert first != second


def test_next_skips_inactive_sentences_and_falls_back_to_check_items(env):
    env.seed(direction("내린 문장", active=False), direction("검증 문항", is_check=True, check_answer="high"))
    assert env.next().json()["sentence"] == "검증 문항"


def test_daily_limit_returns_204_until_the_next_kst_day(env):
    env.seed(*[direction(f"문장 {i}") for i in range(DAILY_LIMIT + 1)])
    assert [env.next().status_code for _ in range(DAILY_LIMIT)] == [200] * DAILY_LIMIT
    assert env.next().status_code == 204
    env.clock[0] = T0 + timedelta(hours=12)  # KST 다음 날 00:00
    assert env.next().status_code == 200


def test_next_returns_204_when_there_are_no_sentences(env):
    assert env.next().status_code == 204


def test_same_request_id_for_another_user_is_409(env):
    env.seed(direction("문장 하나"), direction("문장 둘"))
    rid = str(uuid.uuid4())
    assert env.next(user_id="A", request_id=rid).status_code == 200
    res = env.next(user_id="B", request_id=rid)
    assert res.status_code == 409 and res.json() == {"detail": "request_id reused with different payload"}


@pytest.mark.parametrize(
    "body",
    [
        {"user_id": "1001"},
        {"request_id": "not-a-uuid", "user_id": "1001"},
        {"request_id": str(uuid.uuid4()), "user_id": ""},
        {"request_id": str(uuid.uuid4()), "user_id": "1001", "extra": 1},
    ],
)
def test_next_rejects_bad_body_with_422(env, body):
    assert env.client.post("/v1/sentence-cards/next", json=body).status_code == 422


def test_internal_key_is_required_when_configured(env, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "secret")
    get_settings.cache_clear()
    body = {"request_id": str(uuid.uuid4()), "user_id": "1001"}
    assert env.client.post("/v1/sentence-cards/next", json=body).status_code == 401
    ok = env.client.post("/v1/sentence-cards/next", json=body, headers={"X-Internal-Api-Key": "secret"})
    assert ok.status_code == 204


# ── answers/batch ───────────────────────────────────────


def test_batch_saves_answers_and_same_batch_id_replays_the_first_response(env):
    env.seed(direction("문장 하나"), axis())
    a = env.next().json()
    b = env.next().json()
    batch_id = str(uuid.uuid4())
    picked = b["choices"][1]["choice_id"]
    answers = [answer(a["card_id"], a["choices"][0]["choice_id"]), answer(b["card_id"], picked)]

    res = env.batch(answers, batch_id)
    replay = env.batch(answers, batch_id)

    assert res.status_code == 200
    assert res.json() == {"batch_id": batch_id, "received": 2, "accepted": 2, "rejected": []}
    assert replay.status_code == 200 and replay.json() == res.json()
    stored = {c.id: c for c in env.cards()}
    assert stored[b["card_id"]].choice == picked and stored[b["card_id"]].status == "ANSWERED"
    assert stored[b["card_id"]].batch_id == batch_id


def test_batch_reports_each_rejection_reason_and_keeps_the_rest(env):
    env.seed(*[direction(f"문장 {i}") for i in range(3)])
    cards = [env.next().json() for _ in range(3)]
    env.seed(axis())
    env.clock[0] = T0 + timedelta(days=1)
    other = env.next().json()  # 축 질문 (direct · inferred · none · unsure)
    expired_at = T0 + timedelta(hours=25)
    first = cards[0]["card_id"]

    res = env.batch(
        [
            answer(first, "high"),
            answer(first, "low"),  # 같은 묶음 안 중복
            answer("sc_missing", "high"),
            answer(cards[1]["card_id"], "high", user_id="someone-else"),
            answer(cards[1]["card_id"], "direct"),  # 방향 질문 카드에 축 선택지
            answer(cards[2]["card_id"], "high", at=expired_at),
            answer(other["card_id"], "none", at=T0 + timedelta(days=1)),
        ]
    ).json()

    assert res["received"] == 7 and res["accepted"] == 2
    assert res["rejected"] == [
        {"card_id": first, "reason": "DUPLICATE_IN_BATCH"},
        {"card_id": "sc_missing", "reason": "CARD_NOT_FOUND"},
        {"card_id": cards[1]["card_id"], "reason": "NOT_CARD_OWNER"},
        {"card_id": cards[1]["card_id"], "reason": "DUPLICATE_IN_BATCH"},
        {"card_id": cards[2]["card_id"], "reason": "ANSWERED_AFTER_EXPIRY"},
    ]


def test_choice_not_in_card_is_rejected(env):
    env.seed(direction())
    card = env.next().json()
    res = env.batch([answer(card["card_id"], "direct")]).json()
    assert res["rejected"] == [{"card_id": card["card_id"], "reason": "CHOICE_NOT_IN_CARD"}]


def test_late_batch_is_valid_when_answered_before_expiry(env):
    env.seed(direction())
    card = env.next().json()
    env.clock[0] = T0 + timedelta(days=3)  # 묶음은 만료 뒤에 도착
    res = env.batch([answer(card["card_id"], "unsure", at=T0 + timedelta(hours=23))]).json()
    assert res["accepted"] == 1 and res["rejected"] == []


def test_card_answered_in_an_earlier_batch(env):
    env.seed(direction())
    card = env.next().json()["card_id"]
    env.batch([answer(card, "high")])
    same = env.batch([answer(card, "high")]).json()
    different = env.batch([answer(card, "low")]).json()
    assert same["accepted"] == 1 and same["rejected"] == []
    assert different["rejected"] == [{"card_id": card, "reason": "ALREADY_ANSWERED"}]
    assert env.cards()[0].choice == "high"


def test_same_batch_id_with_different_payload_is_409(env):
    env.seed(direction())
    card = env.next().json()["card_id"]
    batch_id = str(uuid.uuid4())
    assert env.batch([answer(card, "high")], batch_id).status_code == 200
    res = env.batch([answer(card, "low")], batch_id)
    assert res.status_code == 409 and res.json() == {"detail": "batch_id reused with different payload"}


@pytest.mark.parametrize(
    "answers",
    [
        [],
        [answer("sc_1", "yes")],
        [{**answer("sc_1", "high"), "answered_at": "2026-11-11T12:00:00"}],  # 타임존 없음
        [{**answer("sc_1", "high"), "extra": 1}],
        [answer(f"sc_{i}", "high") for i in range(1001)],
    ],
)
def test_batch_rejects_bad_shape_with_422_and_saves_nothing(env, answers):
    res = env.client.post("/v1/sentence-cards/answers/batch", json={"batch_id": str(uuid.uuid4()), "answers": answers})
    assert res.status_code == 422
