"""v2 시뮬레이션 — 결정하는 곳.

start(req)             페르소나 해석(없으면 404) → 진행 중 작업 확인(409) → 작업 생성. LLM 없음, 빠름
run(job_id)            백그라운드: load_context → fact 조회 → 하네스 실행 → 결과 저장 / 실패 기록
get_job(job_id)        상태 조회 (멈춘 작업은 failed 로)
get(simulation_id)     저장된 결과
list_for(user_id)      목록 (요청자든 상대든)
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.observability import build_langfuse_metadata
from app.features.persona.lookup import LoadedPersona, load_persona
from app.features.persona.schemas import PersonaBrief, PersonaRef, PersonaResponse
from app.features.simulation.report import score_layer
from app.features.simulation.schemas import (
    GRADE_LABEL,
    MatchingReport,
    SimulationRequest,
    SimulationSummary,
    Turn,
    grade_of,
)

from . import graph, topics
from .llm import call_tools
from .models import V2Simulation, V2SimulationJob
from .repository import SimulationV2Repository
from .schemas import LLMCaller, SimContext, TopicFact, V2SimulationResponse

logger = logging.getLogger(__name__)

STALE_ERROR = "stale: 서버 재시작 등으로 멈춘 작업"


class PersonaNotFound(Exception):
    def __init__(self, who: str, ref: PersonaRef) -> None:
        self.who = who
        self.ref = ref
        super().__init__(f"{who}: persona not found for {ref.describe()}")


class JobInProgress(Exception):
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"simulation job in progress: {job_id}")


class JobNotFound(Exception):
    pass


class SimulationNotFound(Exception):
    pass


def _utc(at: datetime) -> datetime:
    # SQLite 는 timezone 을 버리고 돌려준다
    return at.astimezone(UTC) if at.tzinfo else at.replace(tzinfo=UTC)


def _is_stale(job: V2SimulationJob) -> bool:
    since = _utc(job.started_at or job.created_at)
    return datetime.now(UTC) - since > timedelta(minutes=get_settings().simulation_v2_job_stale_minutes)


def _finish(job: V2SimulationJob, status: str, *, error: str | None = None, simulation_id: str | None = None) -> None:
    job.status = status
    job.error = error
    job.simulation_id = simulation_id
    job.finished_at = datetime.now(UTC)


def _clean(text: str, limit: int) -> str:
    """프롬프트에 들어가는 사용자 입력(닉네임·페르소나 서술) 정제 — 개행·# 제거 (프롬프트 구조 깨기 방지)."""
    return " ".join(str(text).replace("#", "").splitlines()).strip()[:limit]


def _clean_persona(p: PersonaResponse) -> PersonaResponse:
    update: dict = {
        k: [_clean(x, 100) for x in getattr(p, k)] for k in ("interests", "routine", "date_prefer", "date_avoid")
    }
    if p.narrative is not None:
        update["narrative"] = p.narrative.model_copy(
            update={
                "headline": _clean(p.narrative.headline, 60),
                "body": _clean(p.narrative.body, 800),
                "traits": [_clean(t, 60) for t in p.narrative.traits],
            }
        )
    return p.model_copy(update=update)


class SimulationV2Service:
    def __init__(self, db: AsyncSession, *, llm: LLMCaller | None = None, judge_llm: LLMCaller | None = None) -> None:
        self.db = db
        self.repo = SimulationV2Repository(db)
        self.llm: LLMCaller = llm or call_tools
        self.judge_llm = judge_llm

    async def _load(self, who: str, ref: PersonaRef) -> LoadedPersona:
        loaded = await load_persona(self.db, ref)
        if loaded is None:
            raise PersonaNotFound(who, ref)
        return loaded

    # ── 시작 ──────────────────────────────────────────────

    async def start(self, req: SimulationRequest) -> V2SimulationJob:
        await self._load("me", req.me_ref())
        partner = await self._load("partner", req.partner_ref())
        # 상대를 persona_id·user_id·session_id 중 무엇으로 지정했든, 페르소나 버전이 바뀌었든 같은 사람이면 같은 키
        partner_key = partner.record.user_id or partner.record.id

        active = await self.repo.active_job(req.me_user_id, partner_key)
        if active is not None:
            if not _is_stale(active):
                raise JobInProgress(active.id)
            _finish(active, "failed", error=STALE_ERROR)
            await self.db.flush()
        try:
            async with self.db.begin_nested():
                return await self.repo.create_job(
                    me_user_id=req.me_user_id,
                    partner_key=partner_key,
                    partner_persona_id=partner.record.id,
                    turns=req.turns,
                )
        except IntegrityError as e:
            # 확인과 생성 사이에 다른 요청이 먼저 만들었다
            other = await self.repo.active_job(req.me_user_id, partner_key)
            raise JobInProgress(other.id if other else "") from e

    # ── 실행 (백그라운드) ─────────────────────────────────

    async def run(self, job_id: str) -> None:
        job = await self.repo.get_job(job_id)
        if job is None or job.status != "pending":
            logger.warning("simulation v2 job %s not runnable (status=%s)", job_id, job.status if job else None)
            return
        job.status = "running"
        job.started_at = datetime.now(UTC)
        await self.db.commit()

        started = time.monotonic()
        try:
            await self._run(job)
        except PersonaNotFound as e:
            # 작업을 만든 뒤 페르소나가 지워졌다
            logger.warning("simulation v2 job %s: %s", job_id, e)
            _finish(job, "failed", error="persona_not_found")
        except Exception:
            logger.exception("simulation v2 job %s crashed", job_id)
            await self.db.rollback()
            job = await self.repo.get_job(job_id)
            if job is not None and job.status == "running":
                _finish(job, "failed", error="internal_error")
        await self.db.commit()
        logger.info(
            "simulation v2 job %s finished: status=%s reason=%s elapsed=%.1fs",
            job_id,
            job.status if job else None,
            job.error if job else None,
            time.monotonic() - started,
        )

    async def _run(self, job: V2SimulationJob) -> None:
        s = get_settings()
        me = await self._load("me", PersonaRef(user_id=job.me_user_id))
        partner = await self._load("partner", PersonaRef(persona_id=job.partner_persona_id))
        pa, pb = me.response, partner.response
        dims, area_scores, _ = score_layer(pa, pb)
        partner_user_id = partner.record.user_id or None
        ctx = SimContext(
            me_user_id=job.me_user_id,
            partner_user_id=partner_user_id,
            persona_a_id=me.record.id,
            persona_b_id=partner.record.id,
            persona_a=_clean_persona(pa),
            persona_b=_clean_persona(pb),
            name_a=_clean(me.nickname, 20),
            name_b=_clean(partner.nickname, 20),
            area_scores=area_scores,
            dim_scores={d.dimension: d.score for d in dims},
            core_a=await self.repo.core_profile(job.me_user_id),
            core_b=await self.repo.core_profile(partner_user_id),
        )
        facts_a = await topics.fetch_facts(self.db, job.me_user_id)
        # 같은 사용자끼리(자기 자신과) 시뮬레이션하면 같은 fact 가 양쪽에 들어가므로 상대 쪽은 비운다
        facts_b = await topics.fetch_facts(self.db, partner_user_id) if partner_user_id != job.me_user_id else []
        # 읽기만 한 트랜잭션을 닫아 LLM 을 기다리는 동안 커넥션을 붙잡지 않는다
        await self.db.commit()

        async def on_stage(stage: str) -> None:
            job.stage = stage
            try:
                await self.db.commit()
            except Exception:
                # 진행 표시용이라 실패해도 실행은 계속한다. 세션을 깨진 채 두면 마지막 저장이 실패한다
                await self.db.rollback()
                raise

        try:
            async with asyncio.timeout(s.simulation_v2_job_timeout_s + 5):
                result = await graph.run_harness(
                    ctx,
                    facts_a,
                    facts_b,
                    job.turns,
                    job_id=job.id,
                    llm=self.llm,
                    judge_llm=self.judge_llm,
                    on_stage=on_stage,
                    deadline_s=s.simulation_v2_job_timeout_s,
                    metadata=build_langfuse_metadata(
                        feature="simulation_v2",
                        operation="run",
                        user_id=job.me_user_id,
                        requestedTurns=job.turns,
                    ),
                )
        except TimeoutError:
            _finish(job, "failed", error="timeout")
            return

        if result.status != "succeeded" or result.report is None:
            _finish(job, "failed", error=result.reason or "invalid_script")
            return

        sim = V2Simulation(
            me_user_id=job.me_user_id,
            partner_user_id=partner_user_id,
            persona_a_id=me.record.id,
            persona_b_id=partner.record.id,
            nickname_a=me.nickname[:20],
            nickname_b=partner.nickname[:20],
            turns=job.turns,
            mode=result.mode,
            transcript=[line.model_dump() for line in result.script],
            report={},
            topics_used=[t.model_dump() for t in result.topics_used],
            quality_warnings=list(result.quality_warnings),
            harness_trace=[e.model_dump(exclude_none=True) for e in result.trace],
        )
        await self.repo.save(sim)
        report = result.report.model_copy(update={"simulation_id": sim.id})
        sim.report = report.model_dump(mode="json")
        _finish(job, "succeeded", simulation_id=sim.id)
        await self.db.flush()

    # ── 조회 ──────────────────────────────────────────────

    async def get_job(self, job_id: str) -> V2SimulationJob:
        job = await self.repo.get_job(job_id)
        if job is None:
            raise JobNotFound(job_id)
        if job.status in ("pending", "running") and _is_stale(job):
            _finish(job, "failed", error=STALE_ERROR)
            await self.db.flush()
        return job

    async def get(self, simulation_id: str) -> V2SimulationResponse:
        sim = await self.repo.get(simulation_id)
        if sim is None:
            raise SimulationNotFound(simulation_id)
        me, partner = await self._briefs(sim)
        report = MatchingReport.model_validate(sim.report)
        return V2SimulationResponse(
            simulation_id=sim.id,
            validationResult=report.validationResult,
            me=me,
            partner=partner,
            turns=sim.turns,
            transcript=[Turn(index=t["i"], speaker=t["speaker"], text=t["text"]) for t in sim.transcript],
            report=report,
            created_at=sim.created_at,
            mode=sim.mode,
            topics_used=[TopicFact.model_validate(t) for t in sim.topics_used or []],
            quality_warnings=list(sim.quality_warnings or []),
        )

    async def list_for(self, user_id: str) -> list[SimulationSummary]:
        out = []
        for sim in await self.repo.list_for_user(user_id):
            me, partner = await self._briefs(sim)
            overall = (sim.report or {}).get("overall", {})
            score = int(overall.get("score", 0))
            out.append(
                SimulationSummary(
                    simulation_id=sim.id,
                    me=me,
                    partner=partner,
                    turns=sim.turns,
                    overall_score=score,
                    grade=grade_of(score),
                    grade_label=GRADE_LABEL[grade_of(score)],
                    headline=overall.get("headline", ""),
                    created_at=sim.created_at,
                )
            )
        return out

    async def _briefs(self, sim: V2Simulation) -> tuple[PersonaBrief, PersonaBrief]:
        """저장 당시의 페르소나 행으로. 지워졌으면 저장해 둔 닉네임만으로."""

        async def brief(persona_id: str, user_id: str | None, nickname: str) -> PersonaBrief:
            loaded = await load_persona(self.db, PersonaRef(persona_id=persona_id))
            if loaded is None:
                return PersonaBrief(persona_id=persona_id, user_id=user_id, nickname=nickname)
            return loaded.brief

        return (
            await brief(sim.persona_a_id, sim.me_user_id, sim.nickname_a),
            await brief(sim.persona_b_id, sim.partner_user_id, sim.nickname_b),
        )
