"""저장 점수 정리와 서술-점수 모순 검사 — v1 app/features/persona/service.py 의 함수를 복제했다.

v1 을 바꿔도 v2 추출 결과가 같이 바뀌지 않도록 v2 가 따로 들고 있는다.
"""

from __future__ import annotations

from app.features.persona.schemas import CONFIDENCE_LOW, Narrative

# 서술이 점수와 정면으로 모순되는 흔한 경우만 잡는다. 걸리면 서술만 버리고 점수는 살린다.
# (차원, 점수 조건, 서술에 있으면 안 되는 표현)
_CONTRADICTIONS = [
    ("avoidance", lambda v: v >= 65, ("밀착", "늘 함께", "항상 붙어", "모든 걸 공유")),
    ("avoidance", lambda v: v <= 35, ("각자의 생활을 중시", "독립적인 거리", "거리를 두는")),
    ("anxiety", lambda v: v <= 35, ("불안해하는", "관계를 의심", "많이 신경 쓰는")),
    ("problem_solving", lambda v: v >= 65, ("갈등을 덮", "흐지부지", "피하는 편")),
    ("compliance", lambda v: v <= 35, ("무조건 맞춰", "일방적으로 수용")),
    ("seriousness", lambda v: v >= 65, ("가볍게 만나", "가볍게 알아가")),
    ("seriousness", lambda v: v <= 35, ("진지한 만남", "오래 만날 사람을 찾")),
]


def narrative_contradiction(scores: dict[str, int | None], narrative: Narrative) -> str | None:
    text = " ".join([narrative.headline, narrative.body, *narrative.traits])
    for dim, cond, phrases in _CONTRADICTIONS:
        if scores.get(dim) is not None and cond(scores[dim]):
            for ph in phrases:
                if ph in text:
                    return f"{dim}={scores[dim]} vs '{ph}'"
    return None


# null 도입 전에는 근거 없는 차원을 50 으로 채워 저장했다. 신뢰도 LOW 인 50 은 그 흔적이라 모름으로 읽는다
_LEGACY_UNKNOWN = 50


def known_scores(scores: dict[str, int | None], confidence: dict[str, str]) -> dict[str, int | None]:
    """저장된 점수 → 응답 점수. 옛 행의 '근거 없는 50'을 null 로 바꾼다. 근거 있는 50 은 진짜 중간값."""
    return {
        k: None if v == _LEGACY_UNKNOWN and confidence.get(k, CONFIDENCE_LOW) == CONFIDENCE_LOW else v
        for k, v in scores.items()
    }
