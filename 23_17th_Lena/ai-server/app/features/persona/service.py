"""결정하는 곳.

흐름(무슨 주제를 언제 다룰까)은 전부 여기서 정하고,
agents는 "어떻게 말할까"만 맡는다.
"""

from __future__ import annotations

import logging

from .agents import (
    BuildFailed,
    ConversationAgent,
    ExtractionAgent,
    TaggingAgent,
)
from .models import OnboardingSession
from .repository import PersonaRepository
from .schemas import (
    ALL_DIMENSIONS,
    DEFAULT_SCORE,
    SCORED,
    TEXTUAL,
    TOPICS,
    TOPICS_BY_ID,
    PersonaResponse,
    Topic,
    TurnResponse,
    Weight,
)

logger = logging.getLogger(__name__)


# ══ 커버리지 ═══════════════════════════════════════════════

class Coverage:
    """각 차원에 근거가 몇 건 쌓였는지. DB의 JSON과 오간다."""

    def __init__(self, data: dict | None = None) -> None:
        data = data or {}
        self.primary: dict[str, int] = {
            d: data.get("primary", {}).get(d, 0) for d in ALL_DIMENSIONS
        }
        self.secondary: dict[str, int] = {
            d: data.get("secondary", {}).get(d, 0) for d in ALL_DIMENSIONS
        }

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
            return False                                   # 위에서만 선택
        if turn_index == 0 and t.weight != Weight.LIGHT:
            return False                                   # 첫 턴은 아이스브레이킹
        if t.weight == Weight.HEAVY and turn_index < 5:
            return False                                   # 무거운 건 중반 이후
        if t.weight == Weight.HEAVY and remaining <= 2:
            return False                                   # 무겁게 끝내지 않기
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
        """DB의 턴들을 LLM 메시지 형식으로."""
        messages: list[dict] = []
        for turn in session.turns:
            messages.append({"role": "assistant", "content": turn.question})
            if turn.answer:
                messages.append({"role": "user", "content": turn.answer})
        return messages

    async def _ask_next(self, session: OnboardingSession) -> TurnResponse:
        coverage = Coverage(session.coverage)
        topic = next_topic(
            coverage, session.turn_index, session.total_turns, session.used_topic_ids
        )

        if topic is None or session.turn_index >= session.total_turns:
            return TurnResponse(
                session_id=session.id,
                utterance="오늘 얘기 재밌었어요. 지금 대화로 페르소나를 만들고 있어요.",
                progress=f"{session.total_turns}/{session.total_turns}",
                done=True,
            )

        utterance = await self.conversation.generate(
            history=self._history(session),
            topic=topic,
            turn_index=session.turn_index,
            total_turns=session.total_turns,
            nickname=session.nickname,
        )
        await self.repo.add_question(
            session, topic.id, utterance.text, utterance.source
        )

        return TurnResponse(
            session_id=session.id,
            utterance=utterance.text,
            choices=list(topic.choices) if topic.choices else None,
            progress=f"{session.turn_index + 1}/{session.total_turns}",
        )

    # ── 공개 API ──────────────────────────────────────────

    async def start(
        self, nickname: str, total_turns: int, user_id: str | None = None
    ) -> TurnResponse:
        session = await self.repo.create_session(nickname, total_turns, user_id)
        return await self._ask_next(session)

    async def submit_answer(
        self, session: OnboardingSession, answer: str
    ) -> TurnResponse:
        topic = TOPICS_BY_ID[session.pending_topic_id]
        question = session.turns[-1].question

        tags = await self.tagging.tag(question, answer)

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

    async def build_persona(
        self, session: OnboardingSession
    ) -> PersonaResponse:
        raw = await self.extraction.extract(self._history(session))
        coverage = Coverage(session.coverage)

        scores: dict[str, int] = {}
        confidence: dict[str, str] = {}
        for key in SCORED:
            value = getattr(raw, key, None)
            scores[key] = value if value is not None else DEFAULT_SCORE
            # 모델이 값을 안 냈거나 주 근거가 0건이면 LOW
            if value is None or not coverage.has_primary(key):
                confidence[key] = "LOW"

        texts = {key: getattr(raw, key, []) for key in TEXTUAL}

        await self.repo.save_persona(session, scores, texts, confidence)
        return PersonaResponse(scores=scores, confidence=confidence, **texts)