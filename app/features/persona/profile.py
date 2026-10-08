"""페르소나를 LLM 프롬프트에 넣을 때 쓰는 '말로 된 프로필'.

점수표를 그대로 주면 모델이 숫자를 연기하지 못한다. 차원마다 양 끝 설명(schemas.SCORED 의 low/high)을
점수에 따라 골라 문장으로 바꾸고, 온보딩에서 뽑은 서술·관심사·데이트 취향을 붙인다.
simulation(대본)·practice(상대 역할) 둘 다 이 한 함수를 쓴다 — 두 곳의 인물이 달라지면 안 되니까.
"""

from __future__ import annotations

from .schemas import CONFIDENCE_LOW, SCORED, ConversationStyle, PersonaResponse

# 이 밖이면 "뚜렷한 성향"으로 서술한다. 안쪽(36~64)은 중간이라 굳이 말하지 않는다.
# 답하지 않은 차원은 null(모름)이라 아예 빠진다.
HIGH_FROM = 65
LOW_TO = 35

# (라벨, 필드) — 값이 있는 것만 줄로 만든다. 리스트 항목은 빈도순(앞쪽을 주로 사용)
_STYLE_LISTS = (
    ("자주 쓰는 말 (빈도순)", "frequent_phrases"),
    ("어미 (앞쪽을 주로 사용)", "endings"),
    ("추임새 (앞쪽을 주로 사용)", "interjections"),
    ("줄임말/신조어", "slang"),
)
_STYLE_FIELDS = (
    ("말투", "speech_level"),
    ("문장부호", "punctuation"),
    ("웃음", "laughter"),
    ("메시지 길이", "message_length"),
    ("질문", "question_rate"),
    ("리액션", "reaction"),
    ("자기 얘기", "self_talk"),
    ("유머", "humor"),
    ("대화 주도", "initiative"),
)


def style_block(s: ConversationStyle) -> str:
    """실제 대화에서 관찰한 말투. MBTI 힌트·성향 설명보다 우선한다 — 본인이 실제로 쓴 말이라서."""
    lines = []
    for label, key in _STYLE_LISTS:
        values = getattr(s, key)
        if values:
            lines.append(f"- {label}: " + ", ".join(f'"{v}"' for v in values))
    for label, key in _STYLE_FIELDS:
        value = getattr(s, key)
        if value:
            lines.append(f"- {label}: {value}")
    if s.summary:
        lines.append(f"- 요약: {s.summary}")
    header = "대화 스타일 (실제 대화에서 관찰한 말투 — 말할 때 이걸 그대로 따른다. MBTI 힌트·고정 말투 규칙보다 우선):"
    return header + "\n" + "\n".join(lines)


# MBTI 는 성향 점수가 아니라 말투로만 약하게 가져간다 — 온보딩 결과·궁합 점수는 답변으로만 정해진다.
# J/P 는 관계 진지도 같은 성향으로 보기 어려워 말투로만 쓴다. S/N 은 쓰지 않는다.
MBTI_TONE: dict[str, str] = {
    "E": "먼저 말을 거는 편이고 리액션이 조금 큰 편",
    "I": "말수가 조금 적고 차분하게 답하는 편",
    "F": "공감이나 감정 표현이 조금 섞인 말투",
    "T": "담백하고 사실 위주로 말하는 편",
    "J": "약속이나 계획 얘기를 구체적으로 꺼내는 편",
    "P": "즉흥적인 제안이 섞인 말투",
}


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
    """프롬프트에 그대로 붙이는 블록. 이름 · MBTI · 한 줄 · 대화 스타일 · 성향 · 관심사 · 일상 · 데이트."""
    parts = [f"### {name}"]
    if p.mbti:
        # 본인이 고른 유형이라 말투를 잡는 데만 쓴다. 온보딩에서 직접 답한 성향이 늘 우선이고,
        # MBTI 로 없는 성향·사실을 지어내면 안 된다 (답하지 않은 건 추출하지 않는다는 원칙과 같은 이유)
        tone = [f"- {MBTI_TONE[c]} ({c})" for c in p.mbti if c in MBTI_TONE]
        parts.append(f"MBTI: {p.mbti} — 말투 힌트 (아주 약하게만, 아래 성향과 다르면 성향이 우선):\n" + "\n".join(tone))
    if p.narrative:
        parts.append(f"한 줄: {p.narrative.headline}")
        parts.append(f"설명: {p.narrative.body}")
        if p.narrative.traits:
            parts.append("특징: " + " / ".join(p.narrative.traits))
    traits = trait_lines(p)
    parts.append("뚜렷한 성향:\n" + ("\n".join(traits) if traits else "- (특별히 치우친 성향 없음)"))
    if p.conversation_style:
        parts.append(style_block(p.conversation_style))
    parts.append(f"관심사: {', '.join(p.interests) or '알 수 없음'}")
    parts.append(f"일상: {', '.join(p.routine) or '알 수 없음'}")
    parts.append(
        f"좋아하는 데이트: {', '.join(p.date_prefer) or '알 수 없음'} / 피하는 것: {', '.join(p.date_avoid) or '알 수 없음'}"
    )
    return "\n".join(parts)
