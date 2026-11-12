"""문장 판정 카드 — 다수결 판정 규칙 · 표를 뺄 사용자 · 학습 트리거 조건 (설계 5.5). DB 없음."""

import pytest

from app.features.sentence_cards.schemas import Verdict
from app.features.sentence_cards.verdict import judge, trigger_due, unreliable_users


@pytest.mark.parametrize(
    ("votes", "expected", "verdict", "consensus", "agreement"),
    [
        ([], "direct", Verdict.PENDING, None, None),
        (["direct"] * 4, "direct", Verdict.PENDING, "direct", 1.0),  # 4명 — 표 부족
        (["direct"] * 5, "direct", Verdict.AGREE, "direct", 1.0),
        (["high"] * 7 + ["low"] * 3, "high", Verdict.AGREE, "high", 0.7),  # 경계값 70% 는 통과
        (["low"] * 5, "high", Verdict.DISAGREE, "low", 1.0),
        (["high"] * 6 + ["low"] * 4, "high", Verdict.AMBIGUOUS, "high", 0.6),
        (["unsure"] * 5, "high", Verdict.AMBIGUOUS, "unsure", 1.0),
        (["high"] * 3 + ["low"] * 3, "high", Verdict.AMBIGUOUS, None, 0.5),  # 동률
        (["high"] * 5, None, Verdict.PENDING, "high", 1.0),  # 답안지 없음
    ],
)
def test_judge(votes, expected, verdict, consensus, agreement):
    result = judge(votes, expected)
    assert (result.verdict, result.consensus, result.agreement, result.voters) == (
        verdict,
        consensus,
        agreement,
        len(votes),
    )


def test_unreliable_users_need_enough_check_answers_and_low_accuracy():
    results = [
        *[("careless", False)] * 3,
        *[("unlucky", False)] * 2,  # 2개만 풀었으면 아직 판단하지 않는다
        ("half", True),
        ("half", False),
        ("half", True),
        ("half", False),  # 정답률 50% 는 남긴다
        *[("good", True)] * 3,
    ]
    assert unreliable_users(results) == {"careless"}


@pytest.mark.parametrize(
    ("current", "last", "due"),
    [
        ((250, 100), (0, 0), True),
        ((249, 100), (0, 0), False),
        ((300, 99), (0, 0), False),
        ((500, 200), (250, 100), True),  # 직전 후보 이후 다시 250 · 100 이 늘면 다음 후보
        ((499, 300), (250, 100), False),
    ],
)
def test_trigger_due(current, last, due):
    assert trigger_due(current, last) is due
