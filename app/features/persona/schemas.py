"""도메인 정의 + API 입출력 모델.

차원과 주제를 여기 한 곳에 둔다. agents.py의 프롬프트는
이 정의에서 자동 생성되므로 차원을 추가할 때 프롬프트를 따로 고치지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ══ 차원 정의 ══════════════════════════════════════════════


@dataclass(frozen=True)
class Dimension:
    area: str
    label: str
    low: str = ""
    high: str = ""


SCORED: dict[str, Dimension] = {
    "avoidance": Dimension(
        area="intimacy",
        label="거리 두기",
        low="웬만한 건 공유하고 함께 있는 시간을 선호",
        high="각자 생활을 중시하고 독립적 거리를 유지",
    ),
    "anxiety": Dimension(
        area="intimacy",
        label="관계 불안",
        low="상대 반응이 늦어도 크게 동요하지 않음",
        high="반응이 미지근하면 관계를 의심하고 신경 쓰임",
    ),
    "disclosure": Dimension(
        area="communication",
        label="자기·감정 표현",
        low="속내를 잘 꺼내지 않음",
        high="느낀 것을 말로 표현하는 편",
    ),
    "openness": Dimension(
        area="communication",
        label="솔직한 관계 대화",
        low="관계에 대한 얘기를 꺼내는 걸 부담스러워함",
        high="'우리 어떤지' 같은 얘기를 먼저 꺼냄",
    ),
    "positivity": Dimension(
        area="communication",
        label="긍정적 상호작용",
        low="무덤덤하고 건조한 톤",
        high="밝고 다정한 표현이 잦음",
    ),
    "assurances": Dimension(
        area="communication",
        label="관계 확신 표현",
        low="미래나 마음을 말로 잘 표현하지 않음",
        high="'오래 보자' 같은 말을 먼저 함",
    ),
    "contact_rhythm": Dimension(
        area="communication",
        label="연락 빈도",
        low="할 말 있을 때만 연락",
        high="하루 종일 수시로 주고받기",
    ),
    "problem_solving": Dimension(
        area="conflict",
        label="문제 해결",
        low="갈등을 덮거나 흐지부지 넘김",
        high="원인을 짚고 중간 지점을 찾음",
    ),
    "withdrawal": Dimension(
        area="conflict",
        label="회피·철수",
        low="그 자리에서 계속 대화",
        high="입을 닫거나 자리를 뜸",
    ),
    "engagement": Dimension(
        area="conflict",
        label="감정적 맞대응",
        low="감정을 올리지 않음",
        high="목소리가 커지거나 쏘아붙임",
    ),
    "compliance": Dimension(
        area="conflict",
        label="일방적 수용",
        low="아닌 건 아니라고 말함",
        high="갈등을 피하려 무조건 맞춰줌",
    ),
    "ideal_warmth": Dimension(
        area="ideal",
        label="신뢰·배려",
        low="상대의 성실함·배려를 크게 따지지 않음",
        high="약속을 지키고 남을 배려하는 태도를 최우선으로 봄",
    ),
    "ideal_vitality": Dimension(
        area="ideal",
        label="매력·활발함",
        low="외적 매력이나 텐션을 거의 안 봄",
        high="밝고 활발한 분위기를 중요하게 봄",
    ),
    "ideal_status": Dimension(
        area="ideal",
        label="능력·안정성",
        low="조건을 거의 보지 않음",
        high="직업·경제력 등 안정성을 중요하게 봄",
    ),
    "seriousness": Dimension(
        area="orientation",
        label="관계 진지도",
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
    intent: str  # LLM에 전달되는 "이번에 알아낼 것"
    seed: str  # LLM 실패 시 그대로 쓰는 폴백 질문
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
        id="interests",
        weight=Weight.LIGHT,
        opener="저는 요즘 밤 산책에 빠져서 시간을 제일 많이 써요.",
        covers=("interests",),
        intent="요즘 시간을 많이 쓰는 취미·관심사와 그게 좋은 이유",
        seed="요즘 시간을 가장 많이 쓰는 취미나 관심사가 뭐예요?",
    ),
    Topic(
        id="weekend",
        weight=Weight.LIGHT,
        opener="저는 약속 없는 주말이면 늦잠이 먼저예요.",
        covers=("routine", "date_prefer", "date_avoid"),
        intent="약속 없는 주말을 보내는 방식 + 하고 싶은 데이트 하나, 피하고 싶은 것 하나",
        seed="아무 약속 없는 주말은 보통 어떻게 보내세요?",
    ),
    Topic(
        id="contact",
        weight=Weight.MEDIUM,
        opener="주변 보면 연락 스타일이 진짜 갈리더라고요, 하루 종일 톡 하는 사람이랑 할 말 있을 때만 하는 사람.",
        covers=("contact_rhythm",),
        also_touches=("avoidance",),
        intent="연락 빈도 선호 — 수시로 vs 할 말 있을 때",
        seed="연락은 자주 주고받는 편이 좋아요, 할 말 있을 때가 좋아요?",
    ),
    Topic(
        id="share_vs_separate",
        weight=Weight.MEDIUM,
        opener="연애하면 다 같이 하는 커플도 있고 각자 시간 챙기는 커플도 있잖아요.",
        covers=("avoidance",),
        also_touches=("disclosure",),
        intent="연애할 때 공유 중심인지 각자 생활 중심인지",
        seed="연애하면 웬만한 일은 공유하는 편이에요, 각자 생활이 있는 게 좋아요?",
    ),
    Topic(
        id="hard_times",
        weight=Weight.MEDIUM,
        opener="힘든 일 있을 때 바로 말하는 사람도 있고 혼자 정리하고 말하는 사람도 있더라고요.",
        covers=("disclosure", "openness"),
        also_touches=("positivity",),
        intent="힘든 일이나 관계 고민을 바로 말하는지 혼자 정리 후 말하는지",
        seed="힘든 일이 있을 때 연인에게 바로 말하는 편이에요?",
    ),
    Topic(
        id="slow_reply",
        weight=Weight.MEDIUM,
        opener="답장 늦을 때 드는 생각, 다들 한 번씩 겪잖아요.",
        covers=("anxiety",),
        also_touches=("contact_rhythm",),
        intent="상대 반응이 늦거나 미지근할 때 드는 생각",
        seed="상대 답장이 늦을 때 보통 어떤 생각이 들어요?",
    ),
    Topic(
        id="disagreement",
        weight=Weight.HEAVY,
        opener="소개팅에서 이런 거 물어보면 좀 이상한데, 그래서 더 궁금해요.",
        covers=("problem_solving", "withdrawal", "engagement"),
        intent="의견이 부딪쳤을 때의 행동 + 최근 사례",
        seed="연인과 의견이 부딪쳤을 때 보통 어떻게 행동해요?",
    ),
    Topic(
        id="receiving_hurt",
        weight=Weight.HEAVY,
        opener="반대 상황도 있잖아요, 상대가 나한테 서운하다고 할 때.",
        covers=("compliance",),
        also_touches=("problem_solving", "withdrawal"),
        intent="상대가 서운함을 표현했을 때의 반응",
        seed="상대가 서운함을 표현하면 어떻게 반응하는 편이에요?",
    ),
    Topic(
        id="ideal_type",
        weight=Weight.LIGHT,
        opener="사람 볼 때 제일 먼저 보게 되는 거, 다들 하나씩 있잖아요.",
        covers=("ideal_warmth", "ideal_vitality", "ideal_status"),
        intent="사람 볼 때 먼저 보는 것 + 없으면 안 되는 것 하나",
        seed="사람을 볼 때 가장 먼저 보게 되는 게 뭐예요?",
    ),
    Topic(
        id="orientation",
        weight=Weight.LIGHT,
        is_closing=True,
        opener="오늘 얘기 재밌었어요. 마지막으로 하나만.",
        covers=("seriousness",),
        also_touches=("assurances", "openness"),
        intent="지금 원하는 관계의 온도",
        seed="지금은 어떤 연애를 하고 싶어요?",
        choices=("진지하게 만날 사람", "편하게 알아가기", "아직 잘 모르겠어요"),
    ),
]

TOPICS_BY_ID = {t.id: t for t in TOPICS}


# ══ 보강 질문 은행 ═════════════════════════════════════════
# 근거가 부족한 차원(confidence LOW/MEDIUM)을 채우는 질문. 차원당 2개, 위에서부터 순서대로 쓴다.
# 하루 말투. 온보딩 주제와 겹치지 않게, 그 차원만 정확히 겨눈다.

SUPPLEMENTS: dict[str, tuple[str, ...]] = {
    "avoidance": (
        "연애 중에도 혼자만의 시간이 꼭 필요한 편이에요, 아니면 같이 있는 게 더 편해요?",
        "연인이 갑자기 '오늘 저녁에 볼까?' 하면 보통 어떤 마음이 먼저 들어요?",
    ),
    "anxiety": (
        "상대가 평소보다 말이 짧아지면 무슨 생각이 먼저 들어요?",
        "'우리 괜찮은 거지?' 같은 확인을 하고 싶어질 때가 있어요?",
    ),
    "disclosure": (
        "기분이 안 좋은 날, 연인한테 그걸 티 내는 편이에요 아니면 숨기는 편이에요?",
        "좋아하는 마음은 말로 표현하는 편이에요, 행동으로 보여주는 편이에요?",
    ),
    "openness": (
        "'우리 요즘 어때?' 같은 얘기, 먼저 꺼내는 편이에요?",
        "관계에서 불편한 게 생기면 바로 얘기해요, 아니면 좀 지켜봐요?",
    ),
    "positivity": (
        "연인이랑 있을 때 장난이나 농담을 많이 치는 편이에요?",
        "평소 메시지 톤이 어때요 — 느낌표랑 ㅋㅋ 많이 쓰는 편이에요?",
    ),
    "assurances": (
        "마음에 드는 사람한테 '다음에 또 봐요' 같은 말, 먼저 하는 편이에요?",
        "'오래 보고 싶다' 같은 얘기를 연애 초반에 하는 편이에요?",
    ),
    "contact_rhythm": (
        "일하는 중에 연인 연락이 오면 바로 답해요, 아니면 모아서 답해요?",
        "하루에 연락 몇 번 정도가 딱 편해요?",
    ),
    "problem_solving": (
        "다툰 뒤에 '그래서 다음엔 어떻게 할까'까지 얘기하는 편이에요?",
        "의견이 갈릴 때 중간 지점을 찾으려고 해요, 아니면 한쪽으로 정리해요?",
    ),
    "withdrawal": (
        "감정이 올라올 때 자리를 잠깐 뜨는 편이에요?",
        "말다툼 중에 입을 닫아버린 적 있어요? 그때 어땠어요?",
    ),
    "engagement": (
        "화가 나면 목소리가 커지는 편이에요?",
        "다툴 때 하고 싶은 말을 다 쏟아내는 편이에요, 참는 편이에요?",
    ),
    "compliance": (
        "다툼을 빨리 끝내려고 그냥 맞춰준 적 있어요?",
        "아닌 건 아니라고 말하는 편이에요, 상대 기분 봐서 넘기는 편이에요?",
    ),
    "ideal_warmth": (
        "약속 시간에 자주 늦는 사람, 얼마나 신경 쓰여요?",
        "직원한테 무례한 사람을 보면 어떤 생각이 들어요?",
    ),
    "ideal_vitality": (
        "조용한 사람이랑 활발한 사람 중에 더 끌리는 쪽이 있어요?",
        "첫인상에서 외적인 분위기를 얼마나 봐요?",
    ),
    "ideal_status": (
        "상대의 직업이나 경제력, 솔직히 얼마나 봐요?",
        "'안정적인 사람'이란 말 들으면 뭐가 떠올라요?",
    ),
    "seriousness": (
        "지금 만나면 결혼까지 생각하면서 만나는 편이에요?",
        "가볍게 시작해서 진지해지는 것도 괜찮아요, 처음부터 진지한 게 좋아요?",
    ),
}
assert set(SUPPLEMENTS) == set(SCORED), "보강 질문은 점수 차원 전부에 있어야 한다"


# ══ LLM 출력 검증 ══════════════════════════════════════════
# 모델이 "높음"이나 120을 뱉는 일이 실제로 생긴다.
# 매칭 알고리즘에 들어가기 전 경계에서 막는다.


class Narrative(BaseModel):
    """사용자에게 보여주는 서술. 점수와 같은 호출에서 LLM이 쓴다.

    화면에는 이것이 먼저 보이고 점수는 뒤로 간다 — 사용자는 차트가 아니라
    "○○님은 이런 편이에요"를 읽는다.
    """

    model_config = {"extra": "ignore"}

    headline: str = Field(max_length=60)  # "독립적이지만 대화가 잘 통하는 관계를 원하는 타입"
    body: str = Field(max_length=800)  # 3~5문장, "~하는 편이에요" 톤
    traits: list[str] = Field(default_factory=list, max_length=6)  # 한 줄짜리 특징 3~5개


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

    narrative: Narrative | None = None


class Tags(BaseModel):
    """턴별 태깅 결과."""

    primary: list[str] = Field(default_factory=list)
    secondary: list[str] = Field(default_factory=list)
    off_topic: bool = False


# ══ API 입출력 ═════════════════════════════════════════════


class StartRequest(BaseModel):
    # total_turns 같은 알 수 없는 필드는 조용히 무시하지 않고 422로 거절한다
    model_config = ConfigDict(extra="forbid")

    nickname: str = Field(min_length=1, max_length=20)
    # 앱 사용자 식별자. 시뮬레이션·연습대화가 "이 사용자의 페르소나"를 찾을 때 쓴다. 로그인 필수라 항상 있어야 한다.
    user_id: str = Field(min_length=1, max_length=64)


class AnswerRequest(BaseModel):
    # 문서 §6: 1~200자, 최소 2자
    answer: str = Field(min_length=2, max_length=200)


# 이 수 이상 답하면 건너뛰기·끝내기가 열린다. 그 밑이면 페르소나가 너무 비어서 의미가 없다.
MIN_ANSWERS_TO_FINISH = 3


# 발화 조각의 종류. 첫 턴은 intro → reason → question → self_disclosure → answer_prompt 고정 순서.
# 이후 턴은 LLM 자유 발화라 구조를 추측하지 않고 message 하나로, 폴백 질문은 question, 종료는 closing.
SegmentType = Literal["intro", "reason", "question", "self_disclosure", "answer_prompt", "message", "closing"]


class Segment(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)  # 공백만 있는 text 는 min_length 로 거부

    type: SegmentType
    text: str = Field(min_length=1)


class TurnResponse(BaseModel):
    session_id: str
    utterance: str  # segments 텍스트를 공백으로 이은 전체 발화. 기존 소비자는 이것만 써도 된다
    segments: list[Segment] = Field(default_factory=list)
    choices: list[str] | None = None
    progress: str
    done: bool = False
    answered: int = 0  # 실제로 답한 턴 수 (건너뛴 건 제외)
    can_skip: bool = False  # 이 질문 건너뛰기 가능
    can_finish: bool = False  # 여기서 대화 끝내고 바로 페르소나 만들기 가능


# 신뢰도 — 주 근거 건수로. 사용자에게는 등급명이 아니라 CONFIDENCE_LABEL 로 보여준다.
CONFIDENCE_LOW, CONFIDENCE_MEDIUM, CONFIDENCE_HIGH = "LOW", "MEDIUM", "HIGH"
CONFIDENCE_LABEL = {
    CONFIDENCE_LOW: "아직 잘 몰라요",
    CONFIDENCE_MEDIUM: "어느 정도 알아요",
    CONFIDENCE_HIGH: "잘 알아요",
}
# 정확도 게이지 가중치. "대화할수록 올라가는 숫자" 하나를 만들기 위한 것이지 측정치가 아니다.
CONFIDENCE_WEIGHT = {CONFIDENCE_LOW: 0.0, CONFIDENCE_MEDIUM: 0.6, CONFIDENCE_HIGH: 1.0}


class Gap(BaseModel):
    """아직 근거가 부족한 차원 + 그걸 채울 다음 보강 질문."""

    dimension: str
    label: str
    area: str
    confidence: str
    confidence_label: str
    question: str | None  # 보강 질문을 다 썼으면 None


class Change(BaseModel):
    """이전 버전 대비 달라진 것. kind: score(±10 이상) | confidence(등급 변화)"""

    dimension: str
    label: str
    kind: str
    before: str
    after: str


class PersonaResponse(BaseModel):
    persona_id: str
    version: int = 1
    scores: dict[str, int]
    interests: list[str] = Field(default_factory=list)
    routine: list[str] = Field(default_factory=list)
    date_prefer: list[str] = Field(default_factory=list)
    date_avoid: list[str] = Field(default_factory=list)
    confidence: dict[str, str] = Field(default_factory=dict)  # {차원: LOW|MEDIUM|HIGH}
    narrative: Narrative | None = None  # 점수와 모순되면 service 가 None 으로 떨어뜨림
    accuracy: int = 0  # 0~100. confidence 가중 평균
    gaps: list[Gap] = Field(default_factory=list)  # LOW 먼저, 그다음 MEDIUM
    changes: list[Change] = Field(default_factory=list)  # 이전 버전 대비
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SupplementRequest(BaseModel):
    dimension: str
    answer: str = Field(min_length=2, max_length=200)


# ══ 다른 기능이 페르소나를 가리킬 때 ═══════════════════════
# simulation·practice 는 "저장된 페르소나"만 쓴다. 셋 중 하나로 가리키면 최신 버전을 꺼낸다.


class PersonaRef(BaseModel):
    """persona_id(특정 버전) · user_id(그 사용자의 최신) · session_id(그 온보딩의 최신) 중 정확히 하나."""

    persona_id: str | None = Field(default=None, max_length=32)
    user_id: str | None = Field(default=None, max_length=64)
    session_id: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def _exactly_one(self) -> PersonaRef:
        given = [k for k in ("persona_id", "user_id", "session_id") if getattr(self, k)]
        if len(given) != 1:
            raise ValueError("persona_id, user_id, session_id 중 하나만 지정하세요")
        return self

    def describe(self) -> str:
        return self.persona_id or self.user_id or self.session_id or "?"


class PersonaBrief(BaseModel):
    """시뮬레이션·연습대화 응답에 실리는 참가자 요약. 점수는 안 보여준다 — 리포트가 따로 있다."""

    persona_id: str
    user_id: str | None = None
    nickname: str
    version: int = 1
    headline: str | None = None
    accuracy: int = 0
