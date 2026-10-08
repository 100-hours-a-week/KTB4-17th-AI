"""페르소나 추출 — 결정하는 곳.

  start_practice(user_id)                         연습대화 미반영 발화로 작업 생성 (LLM 없음)
  start_conversation(user_id, speaker_name, raw)  업로드 대화 파싱 → 본인 새 발화 저장 → 작업 생성 (LLM 없음)
  run(job_id)                                     백그라운드: 발화 선택 → StyleAgent → 규칙 적용 → 확정 버전 저장
  get_job(job_id)                                 상태 조회 (멈춘 작업은 failed 로)

반영 규칙(2026-10-05 결정): 대화 스타일은 이전 것과 합쳐 갱신, 점수 4개는 기존*0.7 + 관찰*0.3,
기존이 모름(null)이면 관찰값 + 신뢰도 LOW, 사용자 확인 없이 확정 버전으로 저장.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.observability import build_langfuse_metadata
from app.features.persona.models import PersonaRecord
from app.features.persona.repository import PersonaRepository
from app.features.persona.schemas import CONFIDENCE_LOW, REFLECTED_SCORES, Narrative, PersonaResponse
from app.features.persona.service import known_scores, narrative_contradiction, persona_response

from .agents import ExtractionFailed, StyleAgent
from .models import ExtractionJob, ImportedUtterance
from .parsers import KakaoLine, parse_kakao
from .repository import ExtractionRepository, Identity

logger = logging.getLogger(__name__)

MAX_PHRASES = 10
_PHRASE_MAX_LEN = 30  # 괄호 빈도·상황 힌트(예: '오 대박 (자주)', '~요 (주로 씀)')를 포함할 수 있게 여유
# 숫자(전화·나이·주소 번지), 메일, 링크가 섞인 문구는 버린다 — 말버릇이 아니라 개인정보일 가능성이 크다
_PII = re.compile(r"\d|@|https?://|www\.", re.IGNORECASE)
# 실제 문구를 담는 대화 스타일 필드 — 모두 같은 필터 및 가중치 관리를 거친다
PHRASE_FIELDS = ("frequent_phrases", "endings", "interjections", "slang")

# 어미·단어 수치 가중치 및 시간 감쇠·퇴출 파라미터 (방안 A)
DECAY_FACTOR = 0.7  # 새 대화에서 관찰되지 않은 표현의 감쇠율
WEIGHT_BOOST = 0.35  # 새 대화에서 관찰된 기존 표현의 부스트
INITIAL_WEIGHT = 0.6  # 새로 등장한 표현의 초기 가중치
MIN_WEIGHT_THRESHOLD = 0.2  # 이 미만으로 떨어지면 완전 퇴출(Eviction)


# 자주 쓰는 말에서 개인정보·이름·긴 문장·중복을 거르고 앞에서부터 limit 개
def clean_phrases(phrases: list[str], *, banned: set[str], limit: int = MAX_PHRASES) -> list[str]:
    out: list[str] = []
    for raw in phrases:
        p = raw.strip()
        if not p or len(p) > _PHRASE_MAX_LEN or _PII.search(p):
            continue
        if any(name and name in p for name in banned) or p in out:
            continue
        out.append(p)
        if len(out) == limit:
            break
    return out


def update_phrase_weights(
    current_phrases: dict[str, list[str]],
    previous_weights: dict[str, float] | None,
    previous_phrases: dict[str, list[str]] | None = None,
    *,
    limit: int = MAX_PHRASES,
) -> tuple[dict[str, list[str]], dict[str, float]]:
    """시간 감쇠(Decay), 가중치 부스트 및 퇴출(Eviction) 로직을 적용하여 상위 limit 개를 선별한다.

    1. 새 대화에서 관찰된 표현:
       - 기존에 있던 표현: min(1.0, prev_weight * 0.7 + WEIGHT_BOOST + rank_bonus)
       - 새로 등장한 표현: INITIAL_WEIGHT + rank_bonus
    2. 새 대화에서 관찰되지 않은 이전 표현:
       - prev_weight * DECAY_FACTOR
       - MIN_WEIGHT_THRESHOLD (0.2) 미만으로 떨어지면 자동 퇴출 (탈락)
    3. 각 필드별로 가중치 내림차순 정렬 후 상위 limit 개만 유지
    4. limit 밖으로 밀려난 표현은 가중치 딕셔너리에서 제거 (퇴출)
    """
    prev_w = dict(previous_weights or {})
    prev_p = dict(previous_phrases or {})
    updated_phrases: dict[str, list[str]] = {}
    updated_weights: dict[str, float] = {}

    for field in PHRASE_FIELDS:
        new_list = current_phrases.get(field) or []
        old_list = prev_p.get(field) or []
        observed_set = set(new_list)

        # 1. 새 대화에서 관찰된 항목 가중치 계산
        for idx, phrase in enumerate(new_list):
            rank_bonus = 0.05 if idx == 0 else 0.0
            if phrase in prev_w:
                w = min(1.0, round(prev_w[phrase] * 0.7 + WEIGHT_BOOST + rank_bonus, 2))
            else:
                w = min(1.0, round(INITIAL_WEIGHT + rank_bonus, 2))
            updated_weights[phrase] = w

        # 2. 새 대화에 안 나왔지만 이전에 있던 항목 감쇠(Decay) 및 퇴출(Eviction)
        for phrase in old_list:
            if phrase in observed_set:
                continue
            decayed = round(prev_w.get(phrase, 0.5) * DECAY_FACTOR, 2)
            if decayed >= MIN_WEIGHT_THRESHOLD:
                updated_weights[phrase] = decayed

        # 3. 후보군 수집 및 정렬
        candidates = [p for p in new_list if p in updated_weights]
        candidates += [p for p in old_list if p not in observed_set and p in updated_weights]

        # 정렬 기준: 1) 가중치 내림차순, 2) 새 대화 출현 순서 우선
        candidates.sort(
            key=lambda p: (
                updated_weights.get(p, 0.0),
                -new_list.index(p) if p in new_list else -999,
            ),
            reverse=True,
        )

        # 4. 상위 limit 개 선정 및 컷오프
        selected = candidates[:limit]
        updated_phrases[field] = selected

        # 상위 limit 밖으로 밀려난 항목은 가중치 딕셔너리에서도 제거
        for p in candidates[limit:]:
            updated_weights.pop(p, None)

    return updated_phrases, updated_weights


# REFLECTED_SCORES 만 보정한다. 관찰값이 없으면 그대로. 기존이 null 이면 관찰값 + LOW —
# 추측이 아니라 실제 대화 근거라 채운다 (온보딩의 "답 안 한 차원은 비운다" 원칙과 충돌하지 않는다)
def blend_scores(
    base: dict[str, int | None],
    confidence: dict[str, str],
    observed: dict[str, int | None],
    *,
    base_weight: float,
) -> tuple[dict[str, int | None], dict[str, str]]:
    scores = dict(base)
    conf = dict(confidence)
    for key in REFLECTED_SCORES:
        seen = observed.get(key)
        if seen is None:
            continue
        current = scores.get(key)
        if current is None:
            scores[key] = seen
            conf[key] = CONFIDENCE_LOW
        else:
            scores[key] = round(current * base_weight + seen * (1 - base_weight))
    return scores, conf


class PersonaNotFound(Exception):
    """확정된 페르소나가 없다 — 반영할 대상이 없다."""


class JobInProgress(Exception):
    """같은 사용자의 작업이 진행 중이다. 진행 중 job_id 를 들고 다닌다."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(job_id)


class NothingToExtract(Exception):
    """연습대화에 반영하지 않은 내 발화가 없다."""


class SpeakerNotFound(Exception):
    """업로드 대화에 speaker_name 과 같은 화자가 없다."""


class TooFewUtterances(Exception):
    """새 본인 발화가 최소 개수보다 적다 (이미 올린 발화는 세지 않는다)."""

    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(count)


class JobNotFound(Exception):
    """없는 job_id."""


class NoStyleToDelete(ValueError):
    """현재 페르소나에 삭제할 대화 스타일이 없다."""


# 같은 분 안에서 같은 말을 여러 번 했으면 순번으로 구분한 키. 시각은 UTC 로 맞춰 저장·비교한다
def _identities(lines: list[KakaoLine]) -> list[tuple[Identity, KakaoLine]]:
    seen: dict[tuple[datetime, str], int] = {}
    out = []
    for line in lines:
        at = line.sent_at.astimezone(UTC)
        h = hashlib.sha256(line.text.encode()).hexdigest()
        n = seen.get((at, h), 0)
        seen[(at, h)] = n + 1
        out.append(((at, h, n), line))
    return out


# started_at(없으면 created_at) 이 설정 분 이상 지났으면 멈춘 것으로 본다
def _is_stale(job: ExtractionJob) -> bool:
    since = job.started_at or job.created_at
    if since.tzinfo is None:  # SQLite
        since = since.replace(tzinfo=UTC)
    return datetime.now(UTC) - since > timedelta(minutes=get_settings().extraction_job_stale_minutes)


# 작업을 실패로 끝낸다. 에러 메시지는 응답에 그대로 나가므로 길이를 자른다
def _fail(job: ExtractionJob, error: str) -> None:
    job.status = "failed"
    job.error = error[:500]
    job.finished_at = datetime.now(UTC)


STALE_ERROR = "stale: 서버 재시작 등으로 멈춘 작업"


class ExtractionService:
    # 이 요청의 DB 세션에 묶인 repository/agent 를 만든다
    def __init__(self, db: AsyncSession, agent: StyleAgent | None = None) -> None:
        self.db = db
        self.repo = ExtractionRepository(db)
        self.personas = PersonaRepository(db)
        self.agent = agent or StyleAgent()

    # 진행 중 작업이 있으면 JobInProgress. 단, 오래 멈춘 running 은 failed 로 바꾸고 통과시킨다
    async def _ensure_no_active_job(self, user_id: str) -> None:
        active = await self.repo.active_job(user_id)
        if active is None:
            return
        if _is_stale(active):
            _fail(active, STALE_ERROR)
            await self.db.flush()
            return
        raise JobInProgress(active.id)

    # 반영할 확정 페르소나가 있어야 작업을 만든다
    async def _ensure_persona(self, user_id: str) -> None:
        if await self.personas.latest_persona_for_user(user_id) is None:
            raise PersonaNotFound(user_id)

    # 작업 행 생성. 확인과 생성 사이에 다른 요청이 끼어들면 유니크 인덱스가 막는다
    async def _create_job(self, user_id: str, kind: str) -> ExtractionJob:
        try:
            return await self.repo.create_job(user_id, kind)
        except IntegrityError as e:
            await self.db.rollback()
            active = await self.repo.active_job(user_id)
            raise JobInProgress(active.id if active else "") from e

    # 연습대화 추출 작업 생성. 서버는 50개 조건을 보지 않는다 — 프론트가 관리 (2026-10-08 결정)
    async def start_practice(self, user_id: str) -> ExtractionJob:
        await self._ensure_persona(user_id)
        await self._ensure_no_active_job(user_id)
        if not await self.repo.unreflected_practice_messages(user_id):
            raise NothingToExtract(user_id)
        return await self._create_job(user_id, "practice")

    # 업로드 대화 → 본인 새 발화 저장 → 작업 생성. 원문·상대방 발화는 저장하지 않는다
    async def start_conversation(self, user_id: str, speaker_name: str, raw: str) -> ExtractionJob:
        lines = parse_kakao(raw)  # UnknownFormat 은 그대로 올린다 → 422
        mine = [line for line in lines if line.speaker == speaker_name]
        if not mine:
            raise SpeakerNotFound(speaker_name)
        await self._ensure_persona(user_id)
        await self._ensure_no_active_job(user_id)

        keyed = _identities(mine)
        seen = await self.repo.existing_identities(user_id, [k for k, _ in keyed])
        new = [(k, line) for k, line in keyed if k not in seen]
        if len(new) < get_settings().extraction_min_utterances:
            raise TooFewUtterances(len(new))

        job = await self._create_job(user_id, "conversation")
        await self.repo.add_utterances(
            [
                ImportedUtterance(
                    user_id=user_id,
                    source="kakao",
                    sent_at=at,
                    content=line.text,
                    content_hash=h,
                    occurrence=n,
                    job_id=job.id,
                )
                for (at, h, n), line in new
            ]
        )
        return job

    # 작업 상태. 오래 멈춘 작업은 조회 시점에 failed 로 바꿔 돌려준다
    async def get_job(self, job_id: str) -> ExtractionJob:
        job = await self.repo.get_job(job_id)
        if job is None:
            raise JobNotFound(job_id)
        if job.status in ("pending", "running") and _is_stale(job):
            _fail(job, STALE_ERROR)
            await self.db.flush()
        return job

    # 대화 스타일 삭제(초기화). 원본 온보딩 점수로 복원된 새 확정 버전을 생성한다.
    # 과거 발화의 reflected_persona_id 는 유지하여 이후 새 대화부터 추출된다 (2026-10-08).
    async def delete_style(self, user_id: str) -> PersonaResponse:
        await self._ensure_no_active_job(user_id)
        latest = await self.personas.latest_persona_for_user(user_id)
        if latest is None:
            raise PersonaNotFound(user_id)
        if latest.conversation_style is None:
            raise NoStyleToDelete("삭제할 대화 스타일이 없어요")

        original = await self.personas.base_onboarding_persona(latest.session_id)
        if original is None:
            original = latest

        record = await self.personas.save_reset_version(latest, original)
        return persona_response(record)

    # 백그라운드 실행. 실패해도 예외를 올리지 않고 작업을 failed 로 남긴다 — 발화는 미반영으로 남아 다시 요청할 수 있다
    async def run(self, job_id: str) -> None:
        job = await self.repo.get_job(job_id)
        if job is None or job.status != "pending":
            return
        job.status = "running"
        job.started_at = datetime.now(UTC)
        await self.db.commit()

        try:
            record_id, analyzed = await self._extract(job)
        except (ExtractionFailed, PersonaNotFound, NothingToExtract, IntegrityError) as e:
            # IntegrityError: 같은 온보딩 세션에 보강 재빌드가 동시에 버전을 만들었다
            await self.db.rollback()
            job = await self.repo.get_job(job_id)
            _fail(job, f"{type(e).__name__}: {e}")
            await self.db.commit()
            logger.warning("persona extraction %s failed: %s", job_id, e)
            return
        job.status = "succeeded"
        job.persona_id = record_id
        job.analyzed_count = analyzed
        job.finished_at = datetime.now(UTC)
        await self.db.commit()

    # 발화 선택 → LLM → 규칙 → 확정 버전 저장 → 반영 표시. (새 페르소나 id, 분석한 발화 수)
    async def _extract(self, job: ExtractionJob) -> tuple[str, int]:
        settings = get_settings()
        base = await self.personas.latest_persona_for_user(job.user_id)
        if base is None:
            raise PersonaNotFound(job.user_id)

        if job.kind == "practice":
            items = await self.repo.unreflected_practice_messages(job.user_id)
            banned = await self.repo.practice_nicknames(job.user_id)
            source = "practice"
        else:
            items = await self.repo.job_utterances(job.id)
            banned = set()
            source = "kakao"
        if not items:
            raise NothingToExtract(job.user_id)
        if nickname := await self._nickname(base):
            banned.add(nickname)
        # 최근 N개만 분석하되 나머지 새 발화도 반영 처리한다 — 다시 요청해도 오래된 쪽이 분석되지 않게 (2026-10-06 결정)
        recent = items[-settings.extraction_max_utterances :]

        extraction = await self.agent.analyze(
            [i.content for i in recent],
            previous=persona_response(base).conversation_style,
            trace_metadata=build_langfuse_metadata(
                feature="persona_extraction",
                operation=source,
                user_id=job.user_id,
                session_id=job.id,
                utteranceCount=len(recent),
            ),
        )

        prev_style = persona_response(base).conversation_style
        prev_weights = prev_style.phrase_weights if prev_style else {}
        prev_phrases = {k: getattr(prev_style, k, []) for k in PHRASE_FIELDS} if prev_style else {}

        style = extraction.style.model_dump()
        cleaned: dict[str, list[str]] = {}
        for key in PHRASE_FIELDS:
            cleaned[key] = clean_phrases(style.get(key) or [], banned=banned, limit=MAX_PHRASES * 2)

        final_phrases, new_weights = update_phrase_weights(
            current_phrases=cleaned,
            previous_weights=prev_weights,
            previous_phrases=prev_phrases,
            limit=MAX_PHRASES,
        )
        for key in PHRASE_FIELDS:
            style[key] = final_phrases[key]
        style["phrase_weights"] = new_weights

        observed = {k: getattr(extraction, k) for k in REFLECTED_SCORES}
        scores, confidence = blend_scores(
            known_scores(base.scores, base.confidence),
            base.confidence,
            observed,
            base_weight=settings.extraction_base_weight,
        )
        # 보정한 점수와 서술이 어긋나면 온보딩 빌드와 같은 규칙으로 서술을 버린다
        narrative = base.narrative
        if narrative and narrative_contradiction(scores, Narrative.model_validate(narrative)):
            logger.info("narrative contradicts extracted scores for %s — dropped", job.user_id)
            narrative = None

        record = await self.personas.save_extracted_version(
            base,
            source=source,
            scores=scores,
            confidence=confidence,
            narrative=narrative,
            conversation_style=style,
        )
        if job.kind == "practice":
            await self.repo.mark_practice_reflected(items, record.id)
        else:
            await self.repo.mark_imported_reflected(items, record.id)
        return record.id, len(recent)

    # 온보딩 닉네임 — "자주 쓰는 말"에 내 이름이 섞이지 않게 거른다
    async def _nickname(self, base: PersonaRecord) -> str | None:
        session = await self.personas.get_session_brief(base.session_id)
        return session.nickname if session else None
