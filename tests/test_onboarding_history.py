from types import SimpleNamespace

from app.core.guardrail import contains_profanity
from app.features.persona.service import OnboardingService


def _turn(q, a, tags=None):
    return SimpleNamespace(question=q, answer=a, tags=tags)


def test_history_skips_off_topic_only_when_asked():
    session = SimpleNamespace(
        turns=[_turn("주말엔?", "러닝해요", {"primary": ["interests"]}), _turn("연락은?", "ㅅㅂ", {"off_topic": True})]
    )
    assert len(OnboardingService._history(session)) == 4
    only_valid = OnboardingService._history(session, skip_off_topic=True)
    assert [m["content"] for m in only_valid] == ["주말엔?", "러닝해요"]


def test_contains_profanity():
    assert contains_profanity("몰라 이새끼야")
    assert contains_profanity("ㅅㅂ")
    assert not contains_profanity("새끼 고양이를 키워요")
