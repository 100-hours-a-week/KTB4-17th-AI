"""시뮬레이션 v2 — 하네스 공용 타입과 API 입출력 모델.

모든 모듈(topics·generator·verifier·repair·report·graph·service·api)이 이 파일의 타입만으로 서로 맞물린다.
설계 근거는 docs/기획_및_설계/시뮬레이션_v2_하네스_기획안_최종.md.

화자 표기: 대본의 speaker 는 v1 과 같은 "a"/"b" (a = 요청한 사용자). fact 참조 ID 는 소유자 대문자 접두사
"A-f1", "B-f2" 를 쓴다 — owner_of("A-f1") == "a".
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from app.features.persona.schemas import PersonaResponse
from app.features.simulation.schemas import MatchingReport, SimulationRequest, SimulationResponse, SimulationSummary

Speaker = Literal["a", "b"]
Mode = Literal["shared", "solo", "persona_only"]
JobStatus = Literal["pending", "running", "succeeded", "failed"]
Stage = Literal["topics", "generating", "verifying", "repairing", "reporting"]

# 소재로 쓸 수 있는 fact 카테고리 (기획안 3절: 상대 fact 노출 범위 제한)
TOPIC_CATEGORIES: tuple[str, ...] = ("취미", "일상", "가치관")


def fact_ref(owner: Speaker, n: int) -> str:
    """owner="a", n=1 → "A-f1"."""
    return f"{owner.upper()}-f{n}"


def owner_of(ref: str) -> Speaker | None:
    """ "A-f1" → "a". 형식이 아니면 None."""
    if len(ref) >= 4 and ref[1:3] == "-f" and ref[0] in "AB" and ref[3:].isdigit():
        return "a" if ref[0] == "A" else "b"
    return None


# ══ 컨텍스트 / 소재 ═════════════════════════════════════════


class SimContext(BaseModel):
    """load_context 결과. DB 세션 등 실행 객체는 넣지 않는다."""

    me_user_id: str
    partner_user_id: str | None  # partner_session_id 로 지정한 상대는 user_id 가 없을 수 있다
    persona_a_id: str
    persona_b_id: str
    persona_a: PersonaResponse
    persona_b: PersonaResponse
    name_a: str
    name_b: str
    area_scores: dict[str, int | None] = Field(default_factory=dict)  # v1 score_layer 결과 (ideal 은 None)
    dim_scores: dict[str, int | None] = Field(default_factory=dict)
    core_a: dict[str, Any] = Field(default_factory=dict)  # v2 user_persona_core.profile
    core_b: dict[str, Any] = Field(default_factory=dict)


class FactRow(BaseModel):
    """fetch_facts 가 돌려주는 fact 한 건 (DB 행에서 필요한 것만)."""

    id: int
    category: str
    text: str  # 이미 sanitize 된 문장
    embedding: list[float]
    created_at: datetime


class TopicFact(BaseModel):
    """프롬프트에 들어가는 fact. ref 로만 대본에서 참조한다."""

    ref: str  # "A-f1"
    fact_id: int  # user_persona_fact.id
    owner: Speaker
    category: str
    text: str  # sanitize 된 문장 (200자 이하)


class TopicItem(BaseModel):
    id: str  # "t1", "t2", ...
    kind: Literal["shared", "solo"]
    a_fact: TopicFact | None = None  # shared 면 둘 다, solo 면 한쪽만
    b_fact: TopicFact | None = None
    sim: float | None = None  # shared 일 때 코사인 유사도


class Segment(BaseModel):
    """대화 계획의 한 구간. 왕복 번호는 1부터, 양 끝 포함."""

    start_round: int = Field(ge=1)
    end_round: int = Field(ge=1)
    label: Literal["greeting", "topic", "style", "date", "closing"]
    topic_id: str | None = None  # label == "topic" 일 때 TopicItem.id


class TopicPlan(BaseModel):
    mode: Mode
    items: list[TopicItem] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)
    # 프롬프트에 노출되는 fact 전부 (items 에 쓰인 것 포함). 소유권 검사의 기준
    facts_a: list[TopicFact] = Field(default_factory=list)
    facts_b: list[TopicFact] = Field(default_factory=list)

    def fact_by_ref(self) -> dict[str, TopicFact]:
        return {f.ref: f for f in [*self.facts_a, *self.facts_b]}


# ══ 대본 / 검증 ═════════════════════════════════════════════


class ScriptLineV2(BaseModel):
    i: int = Field(ge=0)
    speaker: Speaker
    text: str = Field(min_length=1, max_length=1000)
    fact_refs: list[str] = Field(default_factory=list)


IssueType = Literal[
    # 규칙 검사
    "format",  # structural — 스키마 위반, 줄 수 ≠ 2N, a/b 교대 깨짐, a 가 먼저가 아님
    "ownership",  # local — 상대 fact 를 상대가 먼저 꺼내기 전에 참조 / 존재하지 않는 ref
    "self_address",  # local — 자기 닉네임+"님"
    "perspective",  # local — 가드레일 PERSPECTIVE 위반
    "echo",  # local — 직전 상대 줄과 3-gram 자카드 ≥ 임계값
    "early_farewell",  # local — 마지막 왕복 이전 작별
    "fabrication",  # local — 프로필·fact 에 없는 직업·나이·거주지 단정 (가드레일 기존 규칙)
    "low_effort",  # warning — 후반 30% 평균 길이 < 전반 70% 평균 × 비율. 분기에 쓰지 않음
    # LLM 심사
    "judge_mixup",  # 의미상 화자 혼동
    "judge_unresponsive",  # 직전 말에 응답하지 않음
    "judge_tone",  # 부자연스러운 말투 전환
]
Severity = Literal["structural", "local", "warning"]

# 예산 소진 후 남아 있으면 job 실패 — 혼동된 대본은 사용자에게 내보내지 않는다
SPEAKER_MIXUP_TYPES: frozenset[str] = frozenset({"ownership", "self_address", "perspective", "judge_mixup"})
FAIL_TYPES: frozenset[str] = SPEAKER_MIXUP_TYPES | {"format", "fabrication"}
# 예산 소진 후 남아도 accept_with_warning 으로 저장
SOFT_TYPES: frozenset[str] = frozenset({"echo", "early_farewell", "judge_unresponsive", "judge_tone"})


class Issue(BaseModel):
    i: int | None = None  # 줄 번호. 대본 전체 문제(줄 수 등)는 None
    type: IssueType
    severity: Severity
    evidence: str = Field(default="", max_length=300)  # 내부 판단·수정 프롬프트용. trace·DB 에는 저장하지 않는다


# ══ LLM 호출 계약 (llm.py 가 구현, 테스트는 가짜 주입) ════════


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class LLMResult(BaseModel):
    calls: list[ToolCall] = Field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class LLMCaller(Protocol):
    """llm.call_tools 와 같은 시그니처. 실패하면 SimLLMError(llm.py) 를 던진다."""

    async def __call__(
        self,
        *,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
        max_tokens: int,
        timeout: float,
        name: str,
        model: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LLMResult: ...


StageCallback = Callable[[str], Awaitable[None]]


# ══ 하네스 상태 / 결과 ══════════════════════════════════════


class Budget(BaseModel):
    script_calls: int = 0  # generate·regenerate·repair·심사 합계 (상한 settings.simulation_v2_script_budget)
    regenerate: int = 0
    repair: int = 0
    report_calls: int = 0
    deadline: float  # time.monotonic() 기준 job 마감 시각


class TraceIssue(BaseModel):
    type: IssueType
    i: int | None = None


class TraceEvent(BaseModel):
    """harness_trace 한 줄. 문장 원문은 넣지 않는다 — 이슈 type·줄 번호·fact id 만."""

    node: str
    ms: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    issues: list[TraceIssue] = Field(default_factory=list)
    fact_ids: list[int] = Field(default_factory=list)
    note: str | None = None  # "judge_skipped", "judge_scope=focus", "template_report" 같은 짧은 표식


class SimV2State(BaseModel):
    job_id: str
    turns: int
    ctx: SimContext
    facts_a: list[FactRow] = Field(default_factory=list)
    facts_b: list[FactRow] = Field(default_factory=list)
    topics: TopicPlan | None = None
    script: list[ScriptLineV2] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    judged_full: bool = False  # 현재 대본 버전이 전체 심사를 받았는지
    patched_lines: list[int] = Field(default_factory=list)  # 마지막 repair 가 고친 줄 (주변 심사 범위)
    judge_skipped: bool = False
    last_llm_error: str | None = None  # 마지막 generate 가 SimLLMError 로 실패했을 때 그 reason
    warnings: list[str] = Field(default_factory=list)  # 성의 저하 등 분기에 쓰지 않는 경고 (quality_warnings)
    attempts: Budget
    report: MatchingReport | None = None
    outcome: Literal["running", "succeeded", "accepted_with_warning", "failed"] = "running"
    reason: str | None = (
        None  # failed 일 때: speaker_mixup_unresolved | invalid_script | fabrication_unresolved | timeout | llm_error
    )
    trace: list[TraceEvent] = Field(default_factory=list)


class HarnessResult(BaseModel):
    """graph.run_harness 반환값. service 가 이걸로 저장/실패 처리한다."""

    status: Literal["succeeded", "failed"]
    reason: str | None = None
    mode: Mode = "persona_only"
    script: list[ScriptLineV2] = Field(default_factory=list)
    report: MatchingReport | None = None  # simulation_id 는 service 가 저장 후 채운다
    topics_used: list[TopicFact] = Field(default_factory=list)  # 대본 fact_refs 에 실제 등장한 fact
    quality_warnings: list[str] = Field(default_factory=list)
    trace: list[TraceEvent] = Field(default_factory=list)
    llm_calls: int = 0  # 대본 + 리포트 단계 합계


# ══ API 입출력 ══════════════════════════════════════════════

V2SimulationRequest = SimulationRequest  # 본문은 v1 과 같다


class V2SimulationJobCreated(BaseModel):
    job_id: str
    status: JobStatus = "pending"


class V2SimulationJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    stage: Stage | None = None
    simulation_id: str | None = None
    reason: str | None = None  # failed 일 때만
    created_at: datetime
    finished_at: datetime | None = None


class V2SimulationResponse(SimulationResponse):
    mode: Mode
    topics_used: list[TopicFact] = Field(default_factory=list)
    quality_warnings: list[str] = Field(default_factory=list)


V2SimulationSummary = SimulationSummary
