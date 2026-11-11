"""문장 판정 카드 — 다수결 판정 규칙과 학습 트리거 조건 (설계 5.5). DB 를 모르는 순수 함수만 둔다.

판정은 문장의 답안지(검증 문항이면 check_answer, 아니면 expected_champion)와 다수결을 비교한다.
  PENDING   : 서로 다른 사용자 표가 MIN_VOTERS 미만, 또는 답안지가 없음
  AMBIGUOUS : 최다 선택 비율이 AGREEMENT 미만, 최다가 동률, 또는 '판단 어려움'이 최다
  AGREE     : 최다 선택 = 답안지
  DISAGREE  : 최다 선택 ≠ 답안지 → 사람 확인(reviewed_by)을 거쳐야 학습에 쓴다
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from .schemas import ChoiceId, Verdict

MIN_VOTERS = 5
AGREEMENT = 0.7

# 검증 문항을 CHECK_MIN_ANSWERS 개 이상 풀었는데 정답률이 CHECK_MIN_ACCURACY 미만이면 그 사용자의 표는 뺀다
CHECK_MIN_ANSWERS = 3
CHECK_MIN_ACCURACY = 0.5

# 직전 트리거 이후 이만큼 늘면 학습 후보 알림 (검증 문항 제외)
TRIGGER_AGREE = 250
TRIGGER_DISAGREE_REVIEWED = 100


@dataclass(frozen=True)
class Judgement:
    verdict: Verdict
    consensus: str | None  # 동률이거나 표가 없으면 None
    agreement: float | None
    voters: int


def judge(votes: list[str], expected: str | None) -> Judgement:
    if not votes:
        return Judgement(Verdict.PENDING, None, None, 0)
    ranked = Counter(votes).most_common()
    top, top_count = ranked[0]
    tied = len(ranked) > 1 and ranked[1][1] == top_count
    consensus = None if tied else top
    agreement = round(top_count / len(votes), 4)
    if len(votes) < MIN_VOTERS:
        verdict = Verdict.PENDING
    elif tied or top == ChoiceId.UNSURE or agreement < AGREEMENT:
        verdict = Verdict.AMBIGUOUS
    elif expected is None:
        verdict = Verdict.PENDING  # 답안지가 없으면 맞다·틀리다를 말할 수 없다
    else:
        verdict = Verdict.AGREE if top == expected else Verdict.DISAGREE
    return Judgement(verdict, consensus, agreement, len(votes))


def unreliable_users(check_results: Iterable[tuple[str, bool]]) -> set[str]:
    """(user_id, 검증 문항을 맞혔는지) 목록 → 표를 뺄 사용자."""
    answered: Counter[str] = Counter()
    correct: Counter[str] = Counter()
    for user_id, ok in check_results:
        answered[user_id] += 1
        correct[user_id] += ok
    return {
        user_id
        for user_id, n in answered.items()
        if n >= CHECK_MIN_ANSWERS and correct[user_id] / n < CHECK_MIN_ACCURACY
    }


def trigger_due(current: tuple[int, int], last: tuple[int, int]) -> bool:
    """current·last = (AGREE 수, 확인된 DISAGREE 수). 직전 트리거 이후 두 조건이 모두 찼는가."""
    return current[0] - last[0] >= TRIGGER_AGREE and current[1] - last[1] >= TRIGGER_DISAGREE_REVIEWED
