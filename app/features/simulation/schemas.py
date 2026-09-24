"""매칭 리포트 — 도메인 정의 + API 입출력 모델.

시뮬레이션(두 페르소나의 가상 소개팅)이 끝난 뒤, 두 사람이 얼마나 맞는지를 보여주는 보고서.

두 층으로 나뉜다:
  - 점수 층 : persona 15차원에서 규칙으로 계산. LLM 없이 결정적으로 나온다.
  - 서술 층 : 대화록을 읽고 LLM이 쓴다. 헤드라인·하이라이트·조언.

점수 규칙(RULES)과 위험 조합(RISKS)을 여기 한 곳에 둔다. agents.py의 프롬프트는
이 정의에서 자동 생성되므로 규칙을 바꿀 때 프롬프트를 따로 고치지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.features.persona.schemas import SCORED, PersonaBrief, PersonaRef, PersonaResponse

# ══ 차원별 궁합 규칙 ═══════════════════════════════════════
# "비슷해야 좋은 것"과 "둘 다 높아야 좋은 것"은 다르다.
# 연락 빈도는 비슷해야 하고, 문제 해결은 둘 다 높아야 하고, 철수는 둘 다 낮아야 한다.


class Fit(StrEnum):
    SIMILAR = "similar"  # 100 - |a-b|
    BOTH_HIGH = "both_high"  # (a+b)/2
    BOTH_LOW = "both_low"  # 100 - (a+b)/2
    JUDGED = "judged"  # 숫자로 못 정함. 대화록 보고 LLM이 판정


@dataclass(frozen=True)
class Rule:
    fit: Fit
    why: str  # 사용자에게 보이는 한 줄 근거 + LLM 프롬프트 재료


RULES: dict[str, Rule] = {
    # intimacy
    "avoidance": Rule(Fit.SIMILAR, "원하는 거리감이 비슷해야 서로 답답하거나 숨 막히지 않음"),
    "anxiety": Rule(Fit.BOTH_LOW, "한쪽이라도 관계 불안이 높으면 확인 요구가 잦아짐"),
    # communication
    "disclosure": Rule(Fit.SIMILAR, "표현 온도가 비슷해야 한쪽만 쏟아붓는 느낌이 안 남"),
    "openness": Rule(Fit.SIMILAR, "관계 얘기를 꺼내는 빈도가 다르면 한쪽은 부담, 한쪽은 답답"),
    "positivity": Rule(Fit.BOTH_HIGH, "밝은 표현이 오갈수록 일상 대화가 편해짐"),
    "assurances": Rule(Fit.SIMILAR, "'오래 보자'는 말의 빈도가 다르면 진도 차이로 느껴짐"),
    "contact_rhythm": Rule(Fit.SIMILAR, "연락 빈도 차이는 초반 갈등 1순위"),
    # conflict
    "problem_solving": Rule(Fit.BOTH_HIGH, "둘 다 원인을 짚으려 해야 갈등이 쌓이지 않음"),
    "withdrawal": Rule(Fit.BOTH_LOW, "한쪽이 자리를 뜨면 대화 자체가 끊김"),
    "engagement": Rule(Fit.BOTH_LOW, "둘 다 감정을 올리면 싸움이 커짐"),
    "compliance": Rule(Fit.BOTH_LOW, "한쪽만 계속 맞춰주면 쌓였다 터짐"),
    # ideal — 내 이상형 기준을 상대가 얼마나 채우는지. 숫자끼리 비교할 수 없어 대화록으로 판정
    "ideal_warmth": Rule(Fit.JUDGED, "상대가 배려·성실함을 대화에서 보였는지"),
    "ideal_vitality": Rule(Fit.JUDGED, "상대의 텐션이 내가 원하는 정도인지"),
    "ideal_status": Rule(Fit.JUDGED, "안정성을 중시하는 정도가 상대와 충돌하지 않는지"),
    # orientation
    "seriousness": Rule(Fit.SIMILAR, "관계 진지도 차이는 가장 먼저 어긋나는 지점"),
}
assert set(RULES) == set(SCORED), "궁합 규칙은 점수 차원 전부에 있어야 한다"

AREAS: dict[str, str] = {
    "intimacy": "거리감",
    "communication": "소통",
    "conflict": "갈등",
    "ideal": "이상형",
    "orientation": "관계 방향",
}
# 총점 가중치. 갈등·방향은 헤어지는 이유라 무겁게.
AREA_WEIGHT: dict[str, float] = {
    "intimacy": 1.0,
    "communication": 1.0,
    "conflict": 1.5,
    "ideal": 1.0,
    "orientation": 1.5,
}
DIMENSIONS_BY_AREA: dict[str, list[str]] = {
    area: [d for d, dim in SCORED.items() if dim.area == area] for area in AREAS
}


# ══ 위험 조합 ══════════════════════════════════════════════
# 각자는 멀쩡한데 둘이 만나면 생기는 패턴. 점수 규칙으로는 안 잡힌다.
# a_dim 이 높은 사람과 b_dim 이 높은 사람이 만났을 때. 방향 무관하게 양쪽 다 검사한다.

RISK_THRESHOLD = 65


@dataclass(frozen=True)
class Risk:
    id: str
    a_dim: str
    b_dim: str
    label: str
    caution: str  # 사용자에게 보이는 문장
    penalty: int = 8  # 총점에서 뺌


RISKS: list[Risk] = [
    Risk(
        id="pursue_withdraw",
        a_dim="anxiety",
        b_dim="avoidance",
        label="추격-회피",
        caution="한쪽은 확인받고 싶고 한쪽은 거리를 두고 싶어 해요. 연락 기대치를 초반에 맞추는 게 좋아요.",
    ),
    Risk(
        id="attack_withdraw",
        a_dim="engagement",
        b_dim="withdrawal",
        label="맞대응-철수",
        caution="다툴 때 한쪽은 목소리가 커지고 한쪽은 입을 닫아요. 잠깐 쉬고 다시 얘기하는 약속이 필요해요.",
    ),
    Risk(
        id="one_sided_yield",
        a_dim="engagement",
        b_dim="compliance",
        label="일방 수용",
        caution="한쪽이 계속 맞춰주는 구조예요. 지금은 편해도 나중에 한 번에 터질 수 있어요.",
    ),
]


# ══ 등급 ═══════════════════════════════════════════════════
# 사용자에게 숫자보다 등급을 먼저 보여준다. 숫자는 내부·차트용.


class Grade(StrEnum):
    GOOD = "GOOD"
    OK = "OK"
    CAUTION = "CAUTION"


GRADE_LABEL: dict[Grade, str] = {
    Grade.GOOD: "잘 맞아요",
    Grade.OK: "무난해요",
    Grade.CAUTION: "살펴봐야 해요",
}


def grade_of(score: int) -> Grade:
    if score >= 70:
        return Grade.GOOD
    if score >= 45:
        return Grade.OK
    return Grade.CAUTION


# ══ 대화록 ═════════════════════════════════════════════════
# 시뮬레이션(service.run)이 만들고, 리포트(report.py)가 읽는다.
# "턴" 은 왕복 하나 — a 가 말하고 b 가 받는다. 10턴이면 발화 20줄. round = index // 2.

Speaker = Literal["a", "b"]

DEFAULT_TURNS = 10
MIN_TURNS, MAX_TURNS = 3, 15


class Turn(BaseModel):
    index: int = Field(ge=0)
    speaker: Speaker
    text: str = Field(min_length=1, max_length=1000)

    @property
    def round(self) -> int:
        return self.index // 2


class Transcript(BaseModel):
    simulation_id: str | None = None
    turns: list[Turn] = Field(default_factory=list)


class ReportInput(BaseModel):
    """리포트 생성에 필요한 전부. SSE 완료 시점에 이 모양으로 모아서 build_report 에 넘긴다."""

    persona_a: PersonaResponse
    persona_b: PersonaResponse
    transcript: Transcript = Field(default_factory=Transcript)
    nickname_a: str = "A"
    nickname_b: str = "B"


# ══ LLM 출력 검증 ══════════════════════════════════════════


class Highlight(BaseModel):
    kind: Literal["click", "friction"]  # 잘 통한 순간 | 어긋난 순간
    turn_index: int
    quote: str = Field(max_length=200)
    why: str = Field(max_length=200)


class ReportNarrative(BaseModel):
    """LLM이 쓰는 서술 층. 점수는 규칙으로 먼저 나오고, LLM 은 그걸 설명만 한다.

    시뮬레이션에서는 대본과 같은 호출(1회)에서 나온다 — ScriptOutput.report."""

    model_config = {"extra": "ignore"}

    headline: str = Field(max_length=60)  # "연락 리듬이 딱 맞는 두 사람"
    summary: str = Field(max_length=800)  # 3~5문장
    area_comments: dict[str, str] = Field(default_factory=dict)  # {area: 한두 문장}
    highlights: list[Highlight] = Field(default_factory=list, max_length=6)
    strengths: list[str] = Field(default_factory=list, max_length=5)
    cautions: list[str] = Field(default_factory=list, max_length=5)
    date_comment: str = Field(default="", max_length=300)
    # ideal 영역만 LLM이 점수를 준다. {차원: 0~100}. 대화에서 근거를 못 찾으면 키 없음.
    ideal_fit: dict[str, int] = Field(default_factory=dict)


class ScriptLine(BaseModel):
    """LLM 대본 한 줄. index 는 service 가 순서대로 붙인다."""

    speaker: Speaker
    text: str = Field(min_length=1, max_length=1000)


class ScriptOutput(BaseModel):
    """시뮬레이션 LLM 1회 호출의 원본 출력 — 대본 + 리포트 서술을 한 번에.

    대본만 따로 뽑고 리포트를 또 부르면 호출이 2회가 된다. 요구사항은 1회."""

    model_config = {"extra": "ignore"}

    transcript: list[ScriptLine] = Field(min_length=2)
    report: ReportNarrative


# ══ API 출력 ═══════════════════════════════════════════════


class DimensionFit(BaseModel):
    dimension: str
    label: str
    a: int
    b: int
    fit: Fit
    score: int | None  # JUDGED 인데 LLM 근거도 없으면 None
    why: str
    confidence: str  # 두 페르소나 중 낮은 쪽. LOW 면 이 줄은 참고만


class AreaReport(BaseModel):
    area: str
    label: str
    score: int | None  # 차원 전부 None 이면 None
    grade: Grade | None
    grade_label: str | None
    comment: str  # LLM. 폴백 시 템플릿
    dimensions: list[DimensionFit]


class Overall(BaseModel):
    score: int
    grade: Grade
    grade_label: str
    headline: str
    summary: str


class DateSuggestion(BaseModel):
    suggested: list[str] = Field(default_factory=list)  # 둘 다 좋아하는 것
    avoid: list[str] = Field(default_factory=list)  # 한쪽이라도 싫어하는 것
    comment: str = ""


class ReportConfidence(BaseModel):
    """이 리포트를 얼마나 믿어도 되는지. 페르소나가 비어 있으면 리포트도 빈 말이다."""

    accuracy: int  # 0~100. 두 페르소나 accuracy 중 낮은 쪽
    low_dimensions: list[str] = Field(default_factory=list)  # 어느 한쪽이라도 LOW 인 차원
    note: str = ""


class MatchingReport(BaseModel):
    simulation_id: str | None = None
    persona_a_id: str
    persona_b_id: str

    overall: Overall
    areas: list[AreaReport]
    highlights: list[Highlight] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    cautions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)  # 발동한 Risk.id. 프론트가 아이콘 붙일 때 씀
    date_suggestion: DateSuggestion
    confidence: ReportConfidence

    narrative_source: Literal["llm", "template"] = "template"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ══ 시뮬레이션 실행 API ═════════════════════════════════════


class SimulationRequest(BaseModel):
    """me = 현재 사용자(로그인 유저, user_id 필수), partner = 상대.

    partner 는 persona_id · user_id · session_id 중 정확히 하나로 지정한다. 둘 다 DB 에
    저장된 페르소나여야 한다.
    """

    me_user_id: str = Field(min_length=1, max_length=64)
    partner_user_id: str | None = Field(default=None, max_length=64)
    partner_persona_id: str | None = Field(default=None, max_length=32)
    partner_session_id: str | None = Field(default=None, max_length=32)
    turns: int = Field(default=DEFAULT_TURNS, ge=MIN_TURNS, le=MAX_TURNS)  # 왕복 수

    def me_ref(self) -> PersonaRef:
        return PersonaRef(user_id=self.me_user_id)

    def partner_ref(self) -> PersonaRef:
        return PersonaRef(
            persona_id=self.partner_persona_id,
            user_id=self.partner_user_id,
            session_id=self.partner_session_id,
        )

    @model_validator(mode="after")
    def _validate_partner_ref(self) -> SimulationRequest:
        self.partner_ref()  # partner_* 중 정확히 하나가 아니면 여기서 ValueError
        return self


class SimulationResponse(BaseModel):
    simulation_id: str
    me: PersonaBrief
    partner: PersonaBrief
    turns: int
    transcript: list[Turn]  # 사용자에게 그대로 보여주는 대화. speaker a = me, b = partner
    report: MatchingReport
    created_at: datetime


class SimulationSummary(BaseModel):
    """목록용 한 줄."""

    simulation_id: str
    me: PersonaBrief
    partner: PersonaBrief
    turns: int
    overall_score: int
    grade: Grade
    grade_label: str
    headline: str
    created_at: datetime
