"""페르소나를 LLM 프롬프트에 넣을 때 쓰는 '말로 된 프로필'.

점수표를 그대로 주면 모델이 숫자를 연기하지 못한다. 차원마다 양 끝 설명(schemas.SCORED 의 low/high)을
점수에 따라 골라 문장으로 바꾸고, 온보딩에서 뽑은 서술·관심사·데이트 취향을 붙인다.
simulation(대본)·practice(상대 역할) 둘 다 이 한 함수를 쓴다 — 두 곳의 인물이 달라지면 안 되니까.
"""

from __future__ import annotations

from .schemas import CONFIDENCE_LOW, SCORED, PersonaResponse

# 이 밖이면 "뚜렷한 성향"으로 서술한다. 안쪽(36~64)은 중간이라 굳이 말하지 않는다 —
# 근거 부족 기본값 50 이 "중간 성향"으로 연기되는 걸 막기 위해서다.
HIGH_FROM = 65
LOW_TO = 35


def trait_lines(p: PersonaResponse) -> list[str]:
    """점수 → "연락 빈도: 하루 종일 수시로 주고받기" 같은 줄. 근거 부족(LOW)은 뒤에 표시."""
    lines = []
    for key, d in SCORED.items():
        v = p.scores.get(key)
        if v is None:
            continue
        if v >= HIGH_FROM:
            desc = d.high
        elif v <= LOW_TO:
            desc = d.low
        else:
            continue
        low = " (근거 부족 — 약하게만)" if p.confidence.get(key) == CONFIDENCE_LOW else ""
        lines.append(f"- {d.label}: {desc}{low}")
    return lines


def describe(name: str, p: PersonaResponse) -> str:
    """프롬프트에 그대로 붙이는 블록. 이름 · 한 줄 · 성향 · 관심사 · 일상 · 데이트."""
    parts = [f"### {name}"]
    if p.narrative:
        parts.append(f"한 줄: {p.narrative.headline}")
        parts.append(f"설명: {p.narrative.body}")
        if p.narrative.traits:
            parts.append("특징: " + " / ".join(p.narrative.traits))
    traits = trait_lines(p)
    parts.append("뚜렷한 성향:\n" + ("\n".join(traits) if traits else "- (특별히 치우친 성향 없음)"))
    parts.append(f"관심사: {', '.join(p.interests) or '알 수 없음'}")
    parts.append(f"일상: {', '.join(p.routine) or '알 수 없음'}")
    parts.append(
        f"좋아하는 데이트: {', '.join(p.date_prefer) or '알 수 없음'} / 피하는 것: {', '.join(p.date_avoid) or '알 수 없음'}"
    )
    return "\n".join(parts)
