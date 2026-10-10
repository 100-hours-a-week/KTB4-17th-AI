"""온보딩 태깅 이어받기 — 늦은 태깅은 버리지 않고 /build 재태깅이 그 호출을 이어받는다.

온보딩 중 태깅이 대기 시간을 넘기면 사용자는 다음 질문으로 넘어가고, 태깅 호출은 뒤에서 계속 돈다.
/build 는 태그 없는 답을 처음부터 다시 태깅하지 않고 그 호출의 결과를 쓴다. 이어받을 호출이 없거나
실패했으면 그때만 새로 태깅하고, 여러 답이면 한꺼번에 동시에 보낸다.
"""

import asyncio
import time

import pytest
from conftest import with_db

from app.core.config import get_settings
from app.features.persona.agents import ConversationAgent, ExtractionAgent, TaggingAgent, Utterance
from app.features.persona.repository import PersonaRepository
from app.features.persona.schemas import RawExtraction, Segment, Tags
from app.features.persona.service import OnboardingService

WAIT = 0.05  # 온보딩 중 태깅을 기다리는 시간 (테스트용으로 짧게)
SLOW = 0.3  # 대기 시간을 넘기는 태깅


@pytest.fixture(autouse=True)
def _short_wait(monkeypatch):
    monkeypatch.setattr(get_settings(), "onboarding_tag_timeout_s", WAIT)


class FakeConversation(ConversationAgent):
    def __init__(self):
        self.calls = 0

    async def generate(self, **kwargs):
        self.calls += 1
        text = f"질문{self.calls}"
        return Utterance(text=text, source="llm", segments=(Segment(type="message", text=text),))


class ScriptedTagging(TaggingAgent):
    """답마다 (걸리는 시간, 결과)를 정해 둔다. 같은 답이 다시 오면 retag 에 적힌 결과를 쓴다."""

    def __init__(self, script, retag=None, retag_delay=0.0):
        self.script = script
        self.retag = retag or {}
        self.retag_delay = retag_delay
        self.calls = []

    async def tag(self, question, answer, *, timeout=None, trace_metadata=None):
        again = answer in self.calls
        self.calls.append(answer)
        if again:
            await asyncio.sleep(self.retag_delay)
            return self.retag.get(answer)
        delay, result = self.script[answer]
        await asyncio.sleep(delay)
        return result


class CapturingExtraction(ExtractionAgent):
    def __init__(self):
        self.answered = None

    async def extract(self, history, *, answered=None, trace_metadata=None):
        self.answered = answered
        return RawExtraction(interests=["러닝"])


def _routine():
    return Tags(primary=["routine"])


def _interests():
    return Tags(primary=["interests"])


def _onboard_and_build(tagging, answers):
    """온보딩을 시작해 answers 를 내고, 끝낸 뒤 /build. 요청마다 새 DB 세션, 이벤트 루프는 하나.

    (답마다 걸린 시간, /build 에 걸린 시간, 추출에 넘어간 근거 차원)"""

    async def scenario(factory):
        conversation, extraction = FakeConversation(), CapturingExtraction()

        def service(db):
            svc = OnboardingService(PersonaRepository(db))
            svc.conversation, svc.tagging, svc.extraction = conversation, tagging, extraction
            return svc

        async with factory() as db:
            first = await service(db).start("민수", "user-1")
            await db.commit()
        answer_times = []
        for answer in answers:
            async with factory() as db:
                svc = service(db)
                started = time.monotonic()
                await svc.submit_answer(await svc.repo.get_session(first.session_id), answer)
                answer_times.append(time.monotonic() - started)
                await db.commit()
        async with factory() as db:
            svc = service(db)
            await svc.finish(await svc.repo.get_session(first.session_id))
            await db.commit()
        async with factory() as db:
            svc = service(db)
            started = time.monotonic()
            await svc.build_draft(await svc.repo.get_session(first.session_id))
            build_time = time.monotonic() - started
            await db.commit()
        return answer_times, build_time, extraction.answered

    return asyncio.run(with_db(scenario))


def test_slow_tagging_does_not_hold_the_user_past_the_wait():
    tagging = ScriptedTagging({"주말엔 쉬어요": (SLOW, _routine()), "러닝": (0, _interests()), "진지": (0, None)})

    answer_times, _, _ = _onboard_and_build(tagging, ["주말엔 쉬어요", "러닝", "진지"])

    assert answer_times[0] < SLOW  # 늦은 태깅을 끝까지 기다리지 않고 다음 질문으로


def test_build_takes_over_the_earlier_tagging_call_instead_of_starting_again():
    tagging = ScriptedTagging(
        {"주말엔 쉬어요": (SLOW, _routine()), "러닝": (0, _interests()), "진지": (0, _interests())}
    )

    _, _, answered = _onboard_and_build(tagging, ["주말엔 쉬어요", "러닝", "진지"])

    assert tagging.calls == ["주말엔 쉬어요", "러닝", "진지"]  # 같은 답을 다시 태깅하지 않았다
    assert "routine" in answered  # 늦게 끝난 태깅 결과가 근거로 쓰였다


def test_build_tags_again_only_when_the_earlier_call_failed():
    tagging = ScriptedTagging(
        {"주말엔 쉬어요": (SLOW, None), "러닝": (0, _interests()), "진지": (0, _interests())},
        retag={"주말엔 쉬어요": _routine()},
    )

    _, _, answered = _onboard_and_build(tagging, ["주말엔 쉬어요", "러닝", "진지"])

    assert tagging.calls.count("주말엔 쉬어요") == 2
    assert "routine" in answered


def test_answers_to_tag_again_are_sent_together_not_one_by_one():
    # 세 답 모두 온보딩 중 태깅이 실패(즉시 None) — /build 에서 셋을 다시 태깅해야 한다
    delay = 0.2
    tagging = ScriptedTagging(
        {"주말엔 쉬어요": (0, None), "러닝": (0, None), "진지": (0, None)},
        retag={"주말엔 쉬어요": _routine(), "러닝": _interests(), "진지": _interests()},
        retag_delay=delay,
    )

    _, build_time, answered = _onboard_and_build(tagging, ["주말엔 쉬어요", "러닝", "진지"])

    assert build_time < delay * 2  # 하나씩이면 0.6초
    assert {"routine", "interests"} <= answered
