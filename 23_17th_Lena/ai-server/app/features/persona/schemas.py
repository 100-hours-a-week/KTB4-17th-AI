"""도메인 정의 + API 입출력 모델.

차원과 주제를 여기 한 곳에 둔다. agents.py의 프롬프트는
이 정의에서 자동 생성되므로 차원을 추가할 때 프롬프트를 따로 고치지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum

from pydantic import BaseModel, Field


# ══ 차원 정의 ══════════════════════════════════════════════

@dataclass(frozen=True)
class Dimension:
    area: str
    label: str
    low: str = ""
    high: str = ""


SCORED: dict[str, Dimension] = {
    "avoidance": Dimension(
        area="intimacy", label="거리 두기",
        low="웬만한 건 공유하고 함께 있는 시간을 선호",
        high="각자 생활을 중시하고 독립적 거리를 유지",
    ),
    "anxiety": Dimension(
        area="intimacy", label="관계 불안",
        low="상대 반응이 늦어도 크게 동요하지 않음",
        high="반응이 미지근하면 관계를 의심하고 신경 쓰임",
    ),
    "disclosure": Dimension(
        area="communication", label="자기·감정 표현",
        low="속내를 잘 꺼내지 않음",
        high="느낀 것을 말로 표현하는 편",
    ),
    "openness": Dimension(
        area="communication", label="솔직한 관계 대화",
        low="관계에 대한 얘기를 꺼내는 걸 부담스러워함",
        high="'우리 어떤지' 같은 얘기를 먼저 꺼냄",
    ),
    "positivity": Dimension(
        area="communication", label="긍정적 상호작용",
        low="무덤덤하고 건조한 톤",
        high="밝고 다정한 표현이 잦음",
    ),
    "assurances": Dimension(
        area="communication", label="관계 확신 표현",
        low="미래나 마음을 말로 잘 표현하지 않음",
        high="'오래 보자' 같은 말을 먼저 함",
    ),
    "contact_rhythm": Dimension(
        area="communication", label="연락 빈도",
        low="할 말 있을 때만 연락",
        high="하루 종일 수시로 주고받기",
    ),
    "problem_solving": Dimension(
        area="conflict", label="문제 해결",
        low="갈등을 덮거나 흐지부지 넘김",
        high="원인을 짚고 중간 지점을 찾음",
    ),
    "withdrawal": Dimension(
        area="conflict", label="회피·철수",
        low="그 자리에서 계속 대화",
        high="입을 닫거나 자리를 뜸",
    ),
    "engagement": Dimension(
        area="conflict", label="감정적 맞대응",
        low="감정을 올리지 않음",
        high="목소리가 커지거나 쏘아붙임",
    ),
    "compliance": Dimension(
        area="conflict", label="일방적 수용",
        low="아닌 건 아니라고 말함",
        high="갈등을 피하려 무조건 맞춰줌",
    ),
    "ideal_warmth": Dimension(
        area="ideal", label="신뢰·배려",
        low="상대의 성실함·배려를 크게 따지지 않음",
        high="약속을 지키고 남을 배려하는 태도를 최우선으로 봄",
    ),
    "ideal_vitality": Dimension(
        area="ideal", label="매력·활발함",
        low="외적 매력이나 텐션을 거의 안 봄",
        high="밝고 활발한 분위기를 중요하게 봄",
    ),
    "ideal_status": Dimension(
        area="ideal", label="능력·안정성",
        low="조건을 거의 보지 않음",
        high="직업·경제력 등 안정성을 중요하게 봄",
    ),
    "seriousness": Dimension(
        area="orientation", label="관계 진지도",
        low="가볍게 알아가기",
        high="진지하게 오래 만날 사람을 찾는 중",
    ),
}

TEXTUAL: dict[str, str] = {
    "interests": "관심사",
    "routine": "일상",
    "date_prefer": "선호 데이트",
    "date_avoid": "피하고 싶은 것",
}

# 근거 부족 시 기본값. 0이나 None이 아닌 이유는
# 매칭 계산이 "모름"을 극단값으로 오해하지 않게 하기 위함.
DEFAULT_SCORE = 50

ALL_DIMENSIONS: list[str] = [*SCORED.keys(), *TEXTUAL.keys()]


# ══ 주제 정의 ══════════════════════════════════════════════

class Weight(IntEnum):
    LIGHT = 0
    MEDIUM = 1
    HEAVY = 2


@dataclass(frozen=True)
class Topic:
    id: str
    weight: Weight
    intent: str          # LLM에 전달되는 "이번에 알아낼 것"
    seed: str            # LLM 실패 시 그대로 쓰는 폴백 질문
    covers: tuple[str, ...]
    also_touches: tuple[str, ...] = ()
    choices: tuple[str, ...] | None = None
    is_closing: bool = False
    # 하루가 문 여는 한 줄. LLM 에 "참고만" 으로 전달된다.
    # 연애관 주제는 하루의 입장 대신 제3자 얘기로 — 사용자 답을 유도하지 않기 위해.
    # 주제 순서가 동적이라 "아까 ~ 얘기" 같은 순서 의존 표현은 쓰지 않는다.
    opener: str = ""


TOPICS: list[Topic] = [
    Topic(
        id="interests", weight=Weight.LIGHT,
        opener="저는 요즘 밤 산책에 빠져서 시간을 제일 많이 써요.",
        covers=("interests",),
        intent="요즘 시간을 많이 쓰는 취미·관심사와 그게 좋은 이유",
        seed="요즘 시간을 가장 많이 쓰는 취미나 관심사가 뭐예요?",
    ),
    Topic(
        id="weekend", weight=Weight.LIGHT,
        opener="저는 약속 없는 주말이면 늦잠이 먼저예요.",
        covers=("routine", "date_prefer", "date_avoid"),
        intent="약속 없는 주말을 보내는 방식 + 하고 싶은 데이트 하나, 피하고 싶은 것 하나",
        seed="아무 약속 없는 주말은 보통 어떻게 보내세요?",
    ),
    Topic(
        id="contact", weight=Weight.MEDIUM,
        opener="주변 보면 연락 스타일이 진짜 갈리더라고요, 하루 종일 톡 하는 사람이랑 할 말 있을 때만 하는 사람.",
        covers=("contact_rhythm",), also_touches=("avoidance",),
        intent="연락 빈도 선호 — 수시로 vs 할 말 있을 때",
        seed="연락은 자주 주고받는 편이 좋아요, 할 말 있을 때가 좋아요?",
    ),
    Topic(
        id="share_vs_separate", weight=Weight.MEDIUM,
        opener="연애하면 다 같이 하는 커플도 있고 각자 시간 챙기는 커플도 있잖아요.",
        covers=("avoidance",), also_touches=("disclosure",),
        intent="연애할 때 공유 중심인지 각자 생활 중심인지",
        seed="연애하면 웬만한 일은 공유하는 편이에요, 각자 생활이 있는 게 좋아요?",
    ),
    Topic(
        id="hard_times", weight=Weight.MEDIUM,
        opener="힘든 일 있을 때 바로 말하는 사람도 있고 혼자 정리하고 말하는 사람도 있더라고요.",
        covers=("disclosure", "openness"), also_touches=("positivity",),
        intent="힘든 일이나 관계 고민을 바로 말하는지 혼자 정리 후 말하는지",
        seed="힘든 일이 있을 때 연인에게 바로 말하는 편이에요?",
    ),
    Topic(
        id="slow_reply", weight=Weight.MEDIUM,
        opener="답장 늦을 때 드는 생각, 다들 한 번씩 겪잖아요.",
        covers=("anxiety",), also_touches=("contact_rhythm",),
        intent="상대 반응이 늦거나 미지근할 때 드는 생각",
        seed="상대 답장이 늦을 때 보통 어떤 생각이 들어요?",
    ),
    Topic(
        id="disagreement", weight=Weight.HEAVY,
        opener="소개팅에서 이런 거 물어보면 좀 이상한데, 그래서 더 궁금해요.",
        covers=("problem_solving", "withdrawal", "engagement"),
        intent="의견이 부딪쳤을 때의 행동 + 최근 사례",
        seed="연인과 의견이 부딪쳤을 때 보통 어떻게 행동해요?",
    ),
    Topic(
        id="receiving_hurt", weight=Weight.HEAVY,
        opener="반대 상황도 있잖아요, 상대가 나한테 서운하다고 할 때.",
        covers=("compliance",), also_touches=("problem_solving", "withdrawal"),
        intent="상대가 서운함을 표현했을 때의 반응",
        seed="상대가 서운함을 표현하면 어떻게 반응하는 편이에요?",
    ),
    Topic(
        id="ideal_type", weight=Weight.LIGHT,
        opener="사람 볼 때 제일 먼저 보게 되는 거, 다들 하나씩 있잖아요.",
        covers=("ideal_warmth", "ideal_vitality", "ideal_status"),
        intent="사람 볼 때 먼저 보는 것 + 없으면 안 되는 것 하나",
        seed="사람을 볼 때 가장 먼저 보게 되는 게 뭐예요?",
    ),
    Topic(
        id="orientation", weight=Weight.LIGHT, is_closing=True,
        opener="오늘 얘기 재밌었어요. 마지막으로 하나만.",
        covers=("seriousness",), also_touches=("assurances", "openness"),
        intent="지금 원하는 관계의 온도",
        seed="지금은 어떤 연애를 하고 싶어요?",
        choices=("진지하게 만날 사람", "편하게 알아가기", "아직 잘 모르겠어요"),
    ),
]

TOPICS_BY_ID = {t.id: t for t in TOPICS}


# ══ LLM 출력 검증 ══════════════════════════════════════════
# 모델이 "높음"이나 120을 뱉는 일이 실제로 생긴다.
# 매칭 알고리즘에 들어가기 전 경계에서 막는다.

class RawExtraction(BaseModel):
    """추출 LLM의 원본 출력. 근거를 못 찾은 차원은 키가 없다."""

    model_config = {"extra": "ignore"}  # 모델이 지어낸 키는 버린다

    avoidance: int | None = Field(default=None, ge=0, le=100)
    anxiety: int | None = Field(default=None, ge=0, le=100)
    disclosure: int | None = Field(default=None, ge=0, le=100)
    openness: int | None = Field(default=None, ge=0, le=100)
    positivity: int | None = Field(default=None, ge=0, le=100)
    assurances: int | None = Field(default=None, ge=0, le=100)
    contact_rhythm: int | None = Field(default=None, ge=0, le=100)
    problem_solving: int | None = Field(default=None, ge=0, le=100)
    withdrawal: int | None = Field(default=None, ge=0, le=100)
    engagement: int | None = Field(default=None, ge=0, le=100)
    compliance: int | None = Field(default=None, ge=0, le=100)
    ideal_warmth: int | None = Field(default=None, ge=0, le=100)
    ideal_vitality: int | None = Field(default=None, ge=0, le=100)
    ideal_status: int | None = Field(default=None, ge=0, le=100)
    seriousness: int | None = Field(default=None, ge=0, le=100)

    interests: list[str] = Field(default_factory=list)
    routine: list[str] = Field(default_factory=list)
    date_prefer: list[str] = Field(default_factory=list)
    date_avoid: list[str] = Field(default_factory=list)


class Tags(BaseModel):
    """턴별 태깅 결과."""
    primary: list[str] = Field(default_factory=list)
    secondary: list[str] = Field(default_factory=list)
    off_topic: bool = False


# ══ API 입출력 ═════════════════════════════════════════════

class StartRequest(BaseModel):
    nickname: str = Field(min_length=1, max_length=20)
    total_turns: int = Field(default=10, ge=5, le=15)


class AnswerRequest(BaseModel):
    # 문서 §6: 1~200자, 최소 2자
    answer: str = Field(min_length=2, max_length=200)


class TurnResponse(BaseModel):
    session_id: str
    utterance: str
    choices: list[str] | None = None
    progress: str
    done: bool = False


class PersonaResponse(BaseModel):
    scores: dict[str, int]
    interests: list[str] = Field(default_factory=list)
    routine: list[str] = Field(default_factory=list)
    date_prefer: list[str] = Field(default_factory=list)
    date_avoid: list[str] = Field(default_factory=list)
    confidence: dict[str, str] = Field(default_factory=dict)  # {차원: "LOW"}
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )