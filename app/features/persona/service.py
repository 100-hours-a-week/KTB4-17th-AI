"""결정하는 곳.

흐름(무슨 주제를 언제 다룰까)은 전부 여기서 정하고,
agents는 "어떻게 말할까"만 맡는다.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.guardrail import effective_mode
from app.core.guardrail_trace import record_guardrail
from app.core.observability import build_langfuse_metadata

from .agents import (
    BuildFailed,
    ConversationAgent,
    ExtractionAgent,
    TaggingAgent,
)
from .models import OnboardingSession, PersonaRecord
from .repository import PersonaRepository
from .schemas import (
    ALL_DIMENSIONS,
    AREAS,
    CONFIDENCE_HIGH,
    CONFIDENCE_LABEL,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_WEIGHT,
    MIN_ANSWERS_TO_FINISH,
    SCORED,
    SUPPLEMENTS,
    TEXTUAL,
    TOPICS,
    TOPICS_BY_ID,
    Change,
    ConfirmPersonaResponse,
    ConversationStyle,
    Gap,
    Narrative,
    PersonaResponse,
    RawExtraction,
    Segment,
    Summary,
    Topic,
    TurnResponse,
    UpdateNicknameResponse,
    Weight,
)

logger = logging.getLogger(__name__)

# 온보딩 전체 질문 수. 나중에 질문 수를 바꿀 때는 이 값만 수정하면 된다.
ONBOARDING_TOTAL_TURNS = 10


# ══ 커버리지 ═══════════════════════════════════════════════


class Coverage:
    """각 차원에 근거가 몇 건 쌓였는지. DB의 JSON과 오간다."""

    def __init__(self, data: dict | None = None) -> None:
        data = data or {}
        self.primary: dict[str, int] = {d: data.get("primary", {}).get(d, 0) for d in ALL_DIMENSIONS}
        self.secondary: dict[str, int] = {d: data.get("secondary", {}).get(d, 0) for d in ALL_DIMENSIONS}

    def apply(self, primary: list[str], secondary: list[str] | None = None) -> None:
        for d in primary:
            if d in self.primary:
                self.primary[d] += 1
        for d in secondary or []:
            if d in self.secondary:
                self.secondary[d] += 1

    def empty_dimensions(self) -> set[str]:
        return {d for d, n in self.primary.items() if n == 0}

    def has_primary(self, dimension: str) -> bool:
        return self.primary.get(dimension, 0) > 0

    def to_dict(self) -> dict:
        return {"primary": dict(self.primary), "secondary": dict(self.secondary)}


# ══ 주제 선택 (흐름 = 코드) ════════════════════════════════


def next_topic(
    coverage: Coverage,
    turn_index: int,
    total_turns: int,
    used_topic_ids: list[str],
) -> Topic | None:
    empty = coverage.empty_dimensions()
    remaining = total_turns - turn_index

    # 마지막 턴은 클로징 주제로 고정.
    # 커버리지 점수만으로 고르면 orientation(선택지라 제일 빠름)이
    # 중간에 뽑혀 "마지막이에요" 흐름이 깨진다.
    if remaining == 1:
        for t in TOPICS:
            if t.is_closing and t.id not in used_topic_ids:
                return t

    def allowed(t: Topic) -> bool:
        if t.id in used_topic_ids:
            return False
        if t.is_closing:
            return False  # 위에서만 선택
        if turn_index == 0 and t.weight != Weight.LIGHT:
            return False  # 첫 턴은 아이스브레이킹
        if t.weight == Weight.HEAVY and turn_index < 5:
            return False  # 무거운 건 중반 이후
        if t.weight == Weight.HEAVY and remaining <= 2:
            return False  # 무겁게 끝내지 않기
        return True

    candidates = [t for t in TOPICS if allowed(t)]
    if not candidates:
        # 배치 규칙 때문에 후보가 비는 경우 — 10주제·10턴이면 turn 8 에서 실제로 생긴다
        # (무거운 주제 2개가 5~7턴에 다 못 들어가면 "마지막 2턴 금지"에 걸려 남는다).
        # 안 묻고 끝내면 그 차원이 영영 비므로, 규칙을 풀고 남은 것 중 가벼운 순으로 묻는다.
        candidates = [t for t in TOPICS if t.id not in used_topic_ids and not t.is_closing]
    if not candidates:
        return None

    # 빈 차원을 가장 많이 채우는 주제 우선, 동점이면 가벼운 쪽
    return min(
        candidates,
        key=lambda t: (-len(set(t.covers) & empty), int(t.weight)),
    )


class UnknownDimension(Exception):
    pass


class NoPersonaYet(Exception):
    """아직 /build 를 안 한 세션."""


class TooFewAnswers(Exception):
    """건너뛰기·끝내기는 MIN_ANSWERS_TO_FINISH 개 이상 답한 뒤에만."""

    def __init__(self, answered: int) -> None:
        self.answered = answered
        super().__init__(f"{answered} answered, need {MIN_ANSWERS_TO_FINISH}")


class TurnMismatch(Exception):
    """아직 묻지 않은 턴에 대한 답이 왔다. 클라이언트 상태가 서버와 어긋났다."""


class OnboardingNotFinished(Exception):
    """대기 중인 질문이 있거나 약속한 문답 수를 아직 채우지 못했다."""


class PersonaDraftNotFound(Exception):
    """확정할 가치관 초안을 찾을 수 없다."""


class PersonaConfirmationConflict(Exception):
    """이미 확정된 페르소나에 서로 다른 MBTI로 다시 확정을 요청했다."""


class PersonaAlreadyConfirmed(Exception):
    """같은 세션의 가치관이 이미 확정되어 새 build가 필요하지 않다."""


class UserNotFound(Exception):
    """해당 user_id의 온보딩 세션이 존재하지 않는다."""

    def __init__(self, user_id: str) -> None:
        super().__init__(f"user not found: {user_id}")
        self.user_id = user_id


# 질문과 무관한 답(태깅 off_topic)에 한 번 되물을 때 앞에 붙이는 말. 뒤에 그 주제의 기본 질문이 온다
REASK_PREFIX = "ㅎㅎ 제가 질문을 좀 애매하게 했나 봐요. 다시 여쭤볼게요."


# ══ 추출 폴백 ══════════════════════════════════════════════
# 추출 LLM 이 죽어도 온보딩은 끝나야 한다. 자유 답변은 LLM 없이 읽을 수 없으니,
# 선택지로 답한 질문만 점수로 옮기고 나머지 차원은 비워 둔다(→ null, 신뢰도 LOW).
# {주제 id: (차원, {선택지: 점수})}. "아직 잘 모르겠어요" 처럼 표에 없는 답은 근거 없음으로 둔다.

CHOICE_SCORES: dict[str, tuple[str, dict[str, int]]] = {
    "orientation": ("seriousness", {"진지하게 만날 사람": 80, "편하게 알아가기": 25}),
}


def fallback_extraction(session: OnboardingSession) -> RawExtraction:
    """LLM 없이 규칙으로 만든 추출 결과. 서술·텍스트 항목은 비어 있다."""
    scores: dict[str, int] = {}
    for turn in session.turns:
        rule = CHOICE_SCORES.get(turn.topic_id)
        if rule is None or not turn.answer:
            continue
        dimension, by_choice = rule
        for choice, score in by_choice.items():
            if choice in turn.answer:
                scores[dimension] = score
    return RawExtraction(**scores)


# ══ 서술 검증 ══════════════════════════════════════════════
# 서술이 점수와 정면으로 모순되는 흔한 경우만 잡는다. 걸리면 서술만 버리고 점수는 살린다 —
# 재생성은 호출이 하나 더 들어가므로. (차원, 점수 조건, 서술에 있으면 안 되는 표현)

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


# 사용자가 하지 않은 말을 짐작하는 어미. 서술은 사용자가 한 말을 옮겨 쓰는 글이라 이런 문장은 뺀다 —
# "너무 캐묻는 것은 부담스러우실 수 있겠네요"가 시뮬레이션 대본에서 상대가 한 말로 새어 나왔다.
# "좋겠어요"처럼 사용자의 바람을 옮긴 문장은 살리려고 맨 "겠어요"는 넣지 않는다.
_SPECULATION = re.compile(r"겠네요|수 있겠|수도 있겠|수도 있어요|것 같아요|것 같네요|듯해요|듯합니다")


def without_speculation(narrative: Narrative) -> Narrative | None:
    """본문·특징에서 짐작하는 문장을 뺀다. 한 줄 요약이 짐작이거나 본문이 남지 않으면 None."""
    if _SPECULATION.search(narrative.headline):
        return None
    sentences = re.split(r"(?<=[.!?])\s+", narrative.body.strip())
    kept = [x for x in sentences if not _SPECULATION.search(x)]
    if not kept:
        return None
    traits = [t for t in narrative.traits if not _SPECULATION.search(t)]
    return narrative.model_copy(update={"body": " ".join(kept), "traits": traits})


def valid_summaries(summaries: list[Summary]) -> list[dict]:
    """AREAS 밖 category·중복 category는 버린다. category당 첫 항목만 남긴다.

    LLM이 area 이름을 지어내거나 같은 area를 두 번 낼 수 있어 여기서 걸러낸다."""
    seen: set[str] = set()
    out: list[dict] = []
    for s in summaries:
        if s.category not in AREAS or s.category in seen:
            continue
        seen.add(s.category)
        out.append(s.model_dump())
    return out


# ══ 신뢰도 · 정확도 · 갭 · 변화 ═══════════════════════════


def confidence_of(has_value: bool, primary_count: int) -> str:
    """주 근거 2건 이상 HIGH · 1건 MEDIUM · 0건(또는 모델이 값을 안 냄) LOW."""
    if not has_value or primary_count == 0:
        return CONFIDENCE_LOW
    return CONFIDENCE_MEDIUM if primary_count == 1 else CONFIDENCE_HIGH


def accuracy_of(confidence: dict[str, str]) -> int:
    """0~100. 사용자에게 보여주는 '정확도' — 대화할수록 올라가는 숫자 하나."""
    if not SCORED:
        return 0
    total = sum(CONFIDENCE_WEIGHT[confidence.get(k, CONFIDENCE_LOW)] for k in SCORED)
    return round(100 * total / len(SCORED))


def next_supplement(session: OnboardingSession, dimension: str) -> str | None:
    """그 차원의 보강 질문 중 아직 안 쓴 첫 번째. 다 썼으면 None."""
    used = sum(1 for t in session.turns if t.topic_id == f"supplement:{dimension}")
    bank = SUPPLEMENTS[dimension]
    return bank[used] if used < len(bank) else None


def gaps_of(session: OnboardingSession, confidence: dict[str, str]) -> list[Gap]:
    """근거 부족한 차원 목록. LOW 먼저, 그 안에서는 SCORED 순서."""
    order = {CONFIDENCE_LOW: 0, CONFIDENCE_MEDIUM: 1}
    gaps = [
        Gap(
            dimension=k,
            label=d.label,
            area=d.area,
            confidence=confidence[k],
            confidence_label=CONFIDENCE_LABEL[confidence[k]],
            question=next_supplement(session, k),
        )
        for k, d in SCORED.items()
        if confidence.get(k) in order
    ]
    return sorted(gaps, key=lambda g: order[g.confidence])


def changes_between(previous: PersonaRecord | None, current: PersonaRecord) -> list[Change]:
    """이전 버전 대비 — 점수 ±10 이상, 신뢰도 등급 변화."""
    if previous is None:
        return []
    out: list[Change] = []
    for k, d in SCORED.items():
        a, b = previous.scores.get(k), current.scores.get(k)
        if a is not None and b is not None and abs(b - a) >= 10:
            out.append(Change(dimension=k, label=d.label, kind="score", before=str(a), after=str(b)))
        ca, cb = previous.confidence.get(k, CONFIDENCE_LOW), current.confidence.get(k, CONFIDENCE_LOW)
        if ca != cb:
            out.append(
                Change(
                    dimension=k,
                    label=d.label,
                    kind="confidence",
                    before=CONFIDENCE_LABEL[ca],
                    after=CONFIDENCE_LABEL[cb],
                )
            )
    return out


# null 도입 전에는 근거 없는 차원을 50 으로 채워 저장했다. 신뢰도 LOW 인 50 은 그 흔적이라 모름으로 읽는다
_LEGACY_UNKNOWN = 50


def known_scores(scores: dict[str, int | None], confidence: dict[str, str]) -> dict[str, int | None]:
    """저장된 점수 → 응답 점수. 옛 행의 '근거 없는 50'을 null 로 바꾼다. 근거 있는 50 은 진짜 중간값."""
    return {
        k: None if v == _LEGACY_UNKNOWN and confidence.get(k, CONFIDENCE_LOW) == CONFIDENCE_LOW else v
        for k, v in scores.items()
    }


def persona_response(
    record: PersonaRecord,
    gaps: list[Gap] | None = None,
    changes: list[Change] | None = None,
    session_mbti: str | None = None,
) -> PersonaResponse:
    """DB 행 → API 모델. 온보딩 밖(simulation·practice)에서도 쓰므로 세션 없이 만들 수 있다.

    gaps·changes 는 온보딩 화면에서만 의미가 있어 호출부가 넣어준다.
    MBTI 는 확정 때 페르소나 행에 옮겨 적는다(DB 제약상 초안 행엔 못 둔다). 그 전(초안)이나
    MBTI 없이 확정된 옛 행이면 온보딩 시작 때 세션에 받아 둔 값(session_mbti)을 쓴다."""
    return PersonaResponse(
        persona_id=record.id,
        version=record.version,
        is_confirmed=record.is_confirmed,
        confirmed_at=record.confirmed_at,
        mbti=record.mbti or session_mbti,
        source=record.source,
        scores=known_scores(record.scores, record.confidence),
        confidence=record.confidence,
        narrative=Narrative.model_validate(record.narrative) if record.narrative else None,
        summaries=[Summary.model_validate(s) for s in record.summaries] if record.summaries else [],
        conversation_style=(
            ConversationStyle.model_validate(record.conversation_style)
            if getattr(record, "conversation_style", None)
            else None
        ),
        accuracy=accuracy_of(record.confidence),
        gaps=gaps or [],
        changes=changes or [],
        generated_at=record.created_at,
        **{k: record.texts.get(k, []) for k in TEXTUAL},
    )


# ══ 서비스 ═════════════════════════════════════════════════


class OnboardingService:
    def __init__(self, repo: PersonaRepository) -> None:
        self.repo = repo
        self.conversation = ConversationAgent()
        self.tagging = TaggingAgent()
        self.extraction = ExtractionAgent()

    # ── 내부 헬퍼 ─────────────────────────────────────────

    @staticmethod
    def _history(session: OnboardingSession) -> list[dict]:
        """DB의 턴들을 LLM 메시지 형식으로. 답이 없는 턴(건너뜀·대기 중)은 통째로 뺀다 —
        assistant 메시지가 연달아 두 번 오면 모델이 흐름을 잃는다."""
        messages: list[dict] = []
        for turn in session.turns:
            if not turn.answer:
                continue
            messages.append({"role": "assistant", "content": turn.question})
            messages.append({"role": "user", "content": turn.answer})
        return messages

    @staticmethod
    def _answered(session: OnboardingSession) -> int:
        """실제로 답한 턴 수. 건너뛴 턴과, 되물어도 질문과 무관했던 답(off_topic)은 세지 않는다."""
        return sum(1 for t in session.turns if t.answer and not (t.tags or {}).get("off_topic"))

    async def _dimensions_from_answers(self, session: OnboardingSession) -> set[str]:
        """사용자의 답변 자체에 근거가 있는 차원 (점수형 + 텍스트형).

        추출 LLM 은 "말하지 않은 건 생략하라"는 지시에도 다른 답에서 미루어 점수를 채운다.
        그래서 질문이 무엇을 겨눴는지(topic.covers)가 아니라, 태깅이 그 답변에서 주 근거(primary)로
        짚은 차원만 인정한다 — "주말엔 그냥 쉬어요"는 일상의 근거일 뿐 선호 데이트의 근거가 아니다.
        - 건너뜀·대기 중·되물어도 무관했던 답은 제외
        - 온보딩 중 태깅이 실패한 답(tags 없음)은 여기서 다시 태깅한다. 또 실패하면 그 답은 근거로 치지 않는다
        - 보강 질문은 그 차원만 겨눈 질문이라 답했으면 그 차원으로 인정
        secondary 처럼 곁가지로만 스친 차원은 넣지 않는다."""
        dims: set[str] = set()
        for turn in session.turns:
            if not turn.answer or turn.skipped or (turn.tags or {}).get("off_topic"):
                continue
            if turn.topic_id.startswith("supplement:"):
                dims.add(turn.topic_id.removeprefix("supplement:"))
                continue
            primary = (turn.tags or {}).get("primary")
            if turn.tags is None:
                tags = await self.tagging.tag(
                    turn.question,
                    turn.answer,
                    timeout=get_settings().persona_retag_timeout_s,
                    trace_metadata=build_langfuse_metadata(
                        feature="persona",
                        operation="retagging",
                        user_id=session.user_id,
                        session_id=session.id,
                        turnIndex=turn.turn_index,
                        topicId=turn.topic_id,
                    ),
                )
                # 또 실패하면 이 답에서 무엇이 드러났는지 알 수 없다 — 질문으로 짐작하지 않고 인정하지 않는다
                primary = tags.primary if tags else []
            dims.update(primary or [])
        return dims

    def _controls(self, session: OnboardingSession) -> dict:
        answered = self._answered(session)
        ok = answered >= MIN_ANSWERS_TO_FINISH
        return {"answered": answered, "can_skip": ok, "can_finish": ok}

    async def _ask_next(self, session: OnboardingSession) -> TurnResponse:
        coverage = Coverage(session.coverage)
        topic = next_topic(coverage, session.turn_index, session.total_turns, session.used_topic_ids)

        if topic is None or session.turn_index >= session.total_turns:
            return self._closing(session)

        utterance = await self.conversation.generate(
            history=self._history(session),
            topic=topic,
            turn_index=session.turn_index,
            total_turns=session.total_turns,
            nickname=session.nickname,
            user_key=session.user_id,
            trace_metadata=build_langfuse_metadata(
                feature="persona",
                operation="conversation",
                user_id=session.user_id,
                session_id=session.id,
                turnIndex=session.turn_index,
                topicId=topic.id,
            ),
        )
        if utterance.validation:
            await record_guardrail(
                self.repo.db,
                feature="persona",
                operation="conversation",
                session_id=session.id,
                user_id=session.user_id,
                mode=effective_mode(session.user_id),
                result=utterance.validation,
                initial_text=utterance.initial_text,
            )
        await self.repo.add_question(session, topic.id, utterance.text, utterance.source)

        return TurnResponse(
            session_id=session.id,
            utterance=utterance.text,
            segments=list(utterance.segments),
            choices=list(topic.choices) if topic.choices else None,
            progress=f"{session.turn_index + 1}/{session.total_turns}",
            turn_index=session.turn_index,
            validationResult=utterance.validation,
            **self._controls(session),
        )

    def _closing(self, session: OnboardingSession) -> TurnResponse:
        closing = "오늘 얘기 재밌었어요. 지금 대화로 페르소나를 만들고 있어요."
        return TurnResponse(
            session_id=session.id,
            utterance=closing,
            segments=[Segment(type="closing", text=closing)],
            progress=f"{session.total_turns}/{session.total_turns}",
            done=True,
            answered=self._answered(session),
            turn_index=session.turn_index,
        )

    def _current(self, session: OnboardingSession) -> TurnResponse:
        """지금 대기 중인 질문(없으면 마무리)을 다시 만든다. LLM 없이 저장된 질문 문장으로."""
        if session.pending_topic_id is None:
            return self._closing(session)
        topic = TOPICS_BY_ID[session.pending_topic_id]
        question = session.turns[-1].question
        return TurnResponse(
            session_id=session.id,
            utterance=question,
            segments=[Segment(type="message", text=question)],
            choices=list(topic.choices) if topic.choices else None,
            progress=f"{session.turn_index + 1}/{session.total_turns}",
            turn_index=session.turn_index,
            **self._controls(session),
        )

    def _reask(self, session: OnboardingSession, topic: Topic) -> TurnResponse:
        """무관한 답에 대한 되묻기. LLM 없이 그 주제의 짧은 기본 질문으로."""
        text = f"{REASK_PREFIX} {topic.seed}"
        return TurnResponse(
            session_id=session.id,
            utterance=text,
            segments=[Segment(type="message", text=text)],
            choices=list(topic.choices) if topic.choices else None,
            progress=f"{session.turn_index + 1}/{session.total_turns}",
            retry=True,
            turn_index=session.turn_index,
            **self._controls(session),
        )

    # ── 공개 API ──────────────────────────────────────────

    async def start(self, nickname: str, user_id: str, mbti: str | None = None) -> TurnResponse:
        session = await self.repo.create_session(nickname, ONBOARDING_TOTAL_TURNS, user_id, mbti)
        return await self._ask_next(session)

    async def submit_answer(
        self, session: OnboardingSession, answer: str, *, turn_index: int | None = None
    ) -> TurnResponse:
        """turn_index 는 클라이언트가 받은 질문의 턴 번호. 이미 지난 턴이면 재전송이므로
        저장하지 않고 지금 질문을 그대로 돌려준다 (첫 요청이 받았어야 할 응답). 없으면 검사하지 않는다."""
        if turn_index is not None and turn_index < session.turn_index:
            return self._current(session)
        if turn_index is not None and turn_index > session.turn_index:
            raise TurnMismatch

        topic = TOPICS_BY_ID[session.pending_topic_id]
        question = session.turns[-1].question

        tags = await self.tagging.tag(
            question,
            answer,
            trace_metadata=build_langfuse_metadata(
                feature="persona",
                operation="tagging",
                user_id=session.user_id,
                session_id=session.id,
                turnIndex=session.turn_index,
                topicId=topic.id,
            ),
        )

        # 질문과 무관한 답이면 한 번만 가볍게 되묻는다. 턴은 소모하지 않고 답도 저장하지 않는다
        if tags is not None and tags.off_topic and not (session.turns[-1].tags or {}).get("reasked"):
            await self.repo.mark_reasked(session)
            return self._reask(session, topic)

        coverage = Coverage(session.coverage)
        if tags is None:
            # 태깅 실패 시 주제가 커버하기로 한 차원을 그대로 인정
            coverage.apply(list(topic.covers), list(topic.also_touches))
        else:
            coverage.apply(tags.primary, tags.secondary)

        await self.repo.record_answer(
            session,
            answer,
            tags.model_dump() if tags else None,
            coverage.to_dict(),
        )
        return await self._ask_next(session)

    async def skip(self, session: OnboardingSession) -> TurnResponse:
        """이 질문은 건너뛰고 다음 질문으로. 답한 턴이 MIN_ANSWERS_TO_FINISH 미만이면 거부."""
        if self._answered(session) < MIN_ANSWERS_TO_FINISH:
            raise TooFewAnswers(self._answered(session))
        await self.repo.skip_question(session)
        return await self._ask_next(session)

    async def finish(self, session: OnboardingSession) -> TurnResponse:
        """여기서 대화를 끝낸다. 이후 /build 는 지금까지의 답변만으로 페르소나를 만든다."""
        if self._answered(session) < MIN_ANSWERS_TO_FINISH:
            raise TooFewAnswers(self._answered(session))
        await self.repo.finish_early(session)
        return await self._ask_next(session)  # is_done → 마무리 발화

    async def build_draft(self, session: OnboardingSession) -> PersonaResponse:
        """API용 build. 기존 초안은 재사용해 네트워크 재시도를 멱등하게 처리한다.

        단, 규칙으로 만든 폴백 초안이면 LLM 추출을 다시 시도한다."""
        if session.pending_topic_id is not None or session.turn_index < session.total_turns:
            raise OnboardingNotFinished

        latest = await self.repo.latest_persona(session.id)
        if latest is not None:
            if latest.is_confirmed:
                raise PersonaAlreadyConfirmed(session.id)
            if latest.source == "fallback":
                try:
                    return await self.build_persona(session)
                except BuildFailed as e:
                    # 아직도 LLM 이 안 된다 — 폴백 초안을 새 버전으로 또 쌓지 않고 있는 것을 돌려준다
                    logger.warning("extraction retry failed (%s), keeping fallback draft", e)
            previous = await self.repo.latest_before(latest) if latest.previous_id else None
            return self._to_response(session, latest, previous)

        return await self.build_persona(session, allow_fallback=True)

    async def build_persona(self, session: OnboardingSession, *, allow_fallback: bool = False) -> PersonaResponse:
        """완료된 대화 전체 → 미확정 가치관 초안. 재빌드(보강 문답 뒤)도 이 함수.

        allow_fallback 이면 추출 LLM 이 실패해도 규칙 초안(source="fallback")으로 끝낸다.
        보강 재빌드는 폴백하지 않는다 — LLM 으로 만든 기존 초안을 기본값투성이로 덮으면 안 되므로."""
        if session.pending_topic_id is not None or session.turn_index < session.total_turns:
            raise OnboardingNotFinished

        answered = await self._dimensions_from_answers(session)
        source = "llm"
        self.extraction.guardrail_db = self.repo.db
        self.extraction.guardrail_user_key = session.user_id
        self.extraction.guardrail_session_id = session.id
        try:
            raw = await self.extraction.extract(
                self._history(session),
                answered=answered,
                trace_metadata=build_langfuse_metadata(
                    feature="persona",
                    operation="extraction",
                    user_id=session.user_id,
                    session_id=session.id,
                    answeredTurns=self._answered(session),
                ),
            )
        except BuildFailed as e:
            if not allow_fallback:
                raise
            logger.warning("extraction failed (%s), using rule-based fallback draft", e)
            raw, source = fallback_extraction(session), "fallback"
        coverage = Coverage(session.coverage)

        scores: dict[str, int | None] = {}
        confidence: dict[str, str] = {}
        dropped: list[str] = []
        for key in SCORED:
            value = getattr(raw, key, None)
            if value is not None and key not in answered:
                # 직접 답하지 않은 차원을 LLM 이 추측해 채웠다 → 근거 없음(null, LOW)으로 되돌린다
                dropped.append(key)
                value = None
            scores[key] = value  # 근거 없으면 None — "모름"
            # 다시 태깅해서야 근거가 확인된 답은 온보딩 커버리지에 안 잡혀 있다 — 답이 있으면 최소 1건으로 친다
            evidence = max(coverage.primary.get(key, 0), 1 if key in answered else 0)
            confidence[key] = confidence_of(value is not None, evidence)

        texts: dict[str, list[str]] = {}
        for key in TEXTUAL:
            values = getattr(raw, key, [])
            if values and key not in answered:
                dropped.append(key)
                values = []
            texts[key] = values
        if dropped:
            logger.info("dropped unanswered dimensions from extraction: %s", dropped)

        narrative = raw.narrative
        if narrative is not None:
            narrative = without_speculation(narrative)
            if narrative is None:
                logger.warning("narrative is speculation only — dropped")
        if narrative is not None:
            why = narrative_contradiction(scores, narrative)
            if why:
                logger.warning("narrative contradicts scores (%s) — dropped", why)
                narrative = None

        # 근거 있는 점수 차원이 하나도 없는 area 의 카드는 추측으로 쓴 것이다
        grounded_areas = {SCORED[k].area for k in SCORED if k in answered and getattr(raw, k, None) is not None}
        summaries = [s for s in valid_summaries(raw.summaries) if s["category"] in grounded_areas]

        record, previous = await self.repo.save_persona(
            session,
            scores,
            texts,
            confidence,
            narrative=narrative.model_dump() if narrative else None,
            summaries=summaries or None,
            source=source,
        )
        return self._to_response(session, record, previous)

    async def confirm_persona(
        self,
        persona_id: str,
        mbti: str | None,
        confirmed_at: datetime,
    ) -> ConfirmPersonaResponse:
        """기존 초안을 확정하고 같은 페르소나 행에 MBTI를 저장한다.

        mbti 가 없으면 온보딩 시작 때 받아 둔 세션의 MBTI 를 쓴다."""
        record = await self.repo.get_persona_for_update(persona_id)
        if record is None:
            raise PersonaDraftNotFound(persona_id)
        session = await self.repo.get_session_brief(record.session_id)
        start_mbti = session.mbti if session else None
        if mbti is not None and start_mbti is not None and mbti != start_mbti:
            raise PersonaConfirmationConflict(persona_id)
        mbti = mbti or start_mbti  # 둘 다 없으면 MBTI 없이 확정한다 — MBTI 때문에 확정이 막히지 않게

        if record.is_confirmed:
            if mbti is not None and record.mbti not in (None, mbti):
                raise PersonaConfirmationConflict(persona_id)

            # 기존 데이터처럼 확정값은 있지만 MBTI가 없는 경우 한 번만 보강한다.
            if record.mbti is None and mbti is not None:
                stored_at = record.confirmed_at or confirmed_at
                record = await self.repo.save_confirmation(record, mbti, stored_at)
            return ConfirmPersonaResponse(
                persona_id=record.id,
                user_id=record.user_id,
                is_confirmed=True,
                mbti=record.mbti or mbti,
                confirmed_at=record.confirmed_at or confirmed_at,
            )

        record = await self.repo.save_confirmation(record, mbti, confirmed_at)
        return ConfirmPersonaResponse(
            persona_id=record.id,
            user_id=record.user_id,
            is_confirmed=True,
            mbti=record.mbti or mbti,
            confirmed_at=record.confirmed_at or confirmed_at,
        )

    def _to_response(
        self, session: OnboardingSession, record: PersonaRecord, previous: PersonaRecord | None
    ) -> PersonaResponse:
        return persona_response(
            record,
            gaps=gaps_of(session, record.confidence),
            changes=changes_between(previous, record),
            session_mbti=session.mbti,
        )

    async def get_latest(self, session: OnboardingSession) -> PersonaResponse:
        record = await self.repo.latest_persona(session.id)
        if record is None:
            raise NoPersonaYet
        previous = await self.repo.latest_before(record) if record.previous_id else None
        return self._to_response(session, record, previous)

    async def supplement(self, session: OnboardingSession, dimension: str, answer: str) -> PersonaResponse:
        """보강 문답 한 건 받고 즉시 재빌드. 사용자는 '알려줬더니 정확해졌다'를 바로 본다."""
        if dimension not in SUPPLEMENTS:
            raise UnknownDimension(dimension)
        if await self.repo.latest_persona(session.id) is None:
            raise NoPersonaYet
        question = next_supplement(session, dimension)
        if question is None:
            raise UnknownDimension(f"{dimension}: 보강 질문을 다 썼습니다")

        coverage = Coverage(session.coverage)
        coverage.apply([dimension])  # 그 차원을 겨눈 질문이므로 주 근거로 인정. 태깅 호출 없음
        await self.repo.add_supplement(session, dimension, question, answer, coverage.to_dict())
        return await self.build_persona(session)

    async def update_nickname(self, user_id: str, nickname: str) -> UpdateNicknameResponse:
        rowcount = await self.repo.update_nickname(user_id, nickname)
        if rowcount == 0:
            raise UserNotFound(user_id)
        return UpdateNicknameResponse(
            user_id=user_id,
            nickname=nickname,
            updated_sessions=rowcount,
            updated_at=datetime.now(UTC),
        )
