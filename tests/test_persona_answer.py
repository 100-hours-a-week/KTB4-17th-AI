"""온보딩 답변 — 짧은 답·빈 답·질문과 무관한 답에 대한 대응."""

import asyncio
from types import SimpleNamespace

import pytest
from conftest import with_db
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.features.persona import api
from app.features.persona.agents import ConversationAgent, TaggingAgent, Utterance
from app.features.persona.repository import PersonaRepository
from app.features.persona.schemas import TOPICS_BY_ID, Segment, Tags, TurnResponse
from app.features.persona.service import OnboardingService

FIRST_TOPIC = "weekend"  # 빈 커버리지에서 첫 턴(가벼운 주제)으로 뽑히는 주제 — 빈 차원을 가장 많이 채운다


def _client():
    calls = []

    async def lock_session(session_id):
        return SimpleNamespace(id=session_id, pending_topic_id="weekend", turn_index=0)

    class FakeService:
        repo = SimpleNamespace(lock_session=lock_session)

        async def submit_answer(self, session, answer, *, turn_index=None):
            calls.append(answer)
            return TurnResponse(session_id=session.id, utterance="다음 질문", progress="3/10")

    class FakeDb:
        async def commit(self):
            pass

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_service] = lambda: FakeService()
    app.dependency_overrides[get_db] = lambda: FakeDb()
    return TestClient(app), calls


def _answer(client, answer):
    return client.post("/v1/persona/onboarding/s1/answer", json={"answer": answer})


def test_one_character_answer_is_accepted_and_trimmed():
    client, calls = _client()

    res = _answer(client, " 네 ")

    assert res.status_code == 200
    assert calls == ["네"]


@pytest.mark.parametrize(
    ("answer", "code", "message"),
    [
        ("", "answer_empty", "조금 더 길게 입력해 주시면 페르소나를 더 정확하게 만들 수 있어요. 다시 답해 주세요."),
        ("   ", "answer_empty", "조금 더 길게 입력해 주시면 페르소나를 더 정확하게 만들 수 있어요. 다시 답해 주세요."),
        ("가" * 201, "answer_too_long", "답변은 200자 이내로 입력해 주세요. 다시 답해 주세요."),
    ],
    ids=["empty", "whitespace", "too-long"],
)
def test_invalid_answer_is_422_with_message_to_show_and_question_kept(answer, code, message):
    client, calls = _client()

    res = _answer(client, answer)

    assert res.status_code == 422
    assert res.json()["detail"] == {"code": code, "message": message}
    assert calls == []  # 질문은 그대로 — 사용자가 바로 다시 보낼 수 있다


# ── 질문과 무관한 답 (태깅의 off_topic) ────────────────────


class FakeConversation(ConversationAgent):
    """다음 질문 생성 LLM 자리. 몇 번 불렸는지만 센다."""

    def __init__(self):
        self.calls = 0

    async def generate(self, **kwargs):
        self.calls += 1
        text = f"질문{self.calls}"
        return Utterance(text=text, source="llm", segments=(Segment(type="message", text=text),))


class FakeTagging(TaggingAgent):
    """태깅 LLM 자리. off_topic 여부를 차례로 돌려준다. None 이면 태깅 실패."""

    def __init__(self, *results):
        self.results = list(results)

    async def tag(self, question, answer, *, timeout=None, trace_metadata=None):
        off_topic = self.results.pop(0)
        if off_topic is None:
            return None
        return Tags(primary=[] if off_topic else ["interests"], off_topic=off_topic)


def _onboarding(*tag_results, answers):
    """온보딩을 시작하고 answers 를 차례로 제출한다. 요청마다 새 DB 세션. (응답들, 대화 LLM 호출 수)"""

    async def scenario(factory):
        conversation, tagging = FakeConversation(), FakeTagging(*tag_results)

        def service(db):
            svc = OnboardingService(PersonaRepository(db))
            svc.conversation, svc.tagging = conversation, tagging
            return svc

        async with factory() as db:
            first = await service(db).start("민수", "user-1")
            await db.commit()
        responses = [first]
        for answer in answers:
            async with factory() as db:
                svc = service(db)
                responses.append(await svc.submit_answer(await svc.repo.get_session(first.session_id), answer))
                await db.commit()
        return responses, conversation.calls

    return asyncio.run(with_db(scenario))


def test_first_off_topic_answer_is_asked_again_without_using_a_turn():
    (first, again), llm_calls = _onboarding(True, answers=["ㅋㅋㅋ"])
    topic = TOPICS_BY_ID[FIRST_TOPIC]

    assert again.retry is True
    assert again.progress == first.progress == "1/10"
    assert again.utterance.endswith(topic.seed)  # 같은 주제를 짧은 기본 질문으로 다시 묻는다
    assert again.answered == 0
    assert llm_calls == 1  # 되묻기는 LLM 없이 — 첫 질문 생성 1번뿐


def test_second_off_topic_answer_moves_on_but_is_not_counted_as_answered():
    (_, again, moved_on), llm_calls = _onboarding(True, True, answers=["ㅋㅋㅋ", "몰라요"])

    assert again.retry is True
    assert moved_on.retry is False
    assert moved_on.progress == "2/10"  # 두 번 묻지는 않는다 — 다음 질문으로
    assert moved_on.answered == 0  # 건너뛰기·끝내기 조건(3개)에 안 들어간다
    assert llm_calls == 2


def test_on_topic_answer_after_reask_is_counted():
    (_, again, moved_on), _ = _onboarding(True, False, answers=["ㅋㅋㅋ", "주말엔 러닝해요"])

    assert again.retry is True
    assert (moved_on.retry, moved_on.progress, moved_on.answered) == (False, "2/10", 1)


def test_tagging_failure_is_not_treated_as_off_topic():
    (_, moved_on), _ = _onboarding(None, answers=["주말엔 러닝해요"])

    assert (moved_on.retry, moved_on.progress, moved_on.answered) == (False, "2/10", 1)


def test_short_on_topic_answer_is_accepted_like_any_other():
    (_, moved_on), _ = _onboarding(False, answers=["네"])

    assert (moved_on.retry, moved_on.answered) == (False, 1)
