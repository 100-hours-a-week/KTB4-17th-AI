"""시뮬레이션 실행 — 결정하는 곳.

  run(req):
    1. 두 페르소나를 DB 에서 꺼낸다 (없으면 PersonaNotFound → 404)
    2. 규칙 점수를 먼저 계산한다 (LLM 없음)
    3. LLM 을 **한 번** 불러 대본(N턴 왕복) + 리포트 서술을 받는다
    4. 대본을 검증·정리하고(교대 순서, 길이) 리포트를 조립한다
    5. 대본과 리포트를 저장하고 그대로 돌려준다 — 사용자는 대화와 리포트를 함께 본다

agents 는 "어떻게 말할까"만, report 는 "점수는 어떻게 매길까"만 맡는다.
"""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.observability import build_langfuse_metadata
from app.features.persona.lookup import LoadedPersona, load_persona
from app.features.persona.schemas import PersonaBrief, PersonaRef, PersonaResponse

from .agents import SimulationAgent, SimulationFailed
from .models import SimulationRecord
from .report import assemble_report, score_layer
from .repository import SimulationRepository
from .schemas import (
    GRADE_LABEL,
    MatchingReport,
    ReportInput,
    ScriptLine,
    SimulationRequest,
    SimulationResponse,
    SimulationSummary,
    Transcript,
    Turn,
    grade_of,
)

logger = logging.getLogger(__name__)


class PersonaNotFound(Exception):
    def __init__(self, who: str, ref: PersonaRef) -> None:
        self.who = who
        self.ref = ref
        super().__init__(f"{who}: persona not found for {ref.describe()}")


class SimulationNotFound(Exception):
    pass


class SimulationAlreadyRunning(Exception):
    """같은 페르소나 조합의 시뮬레이션이 이미 처리 중이다.

    더블클릭·네트워크 재시도로 똑같은 LLM 호출이 중복되는 걸 막는다. 프로세스 안에서만
    유효하다 — 워커를 여러 개 띄우면 워커별로 따로 추적되어 완전히는 못 막는다."""

    def __init__(self, pair: frozenset[str]) -> None:
        self.pair = pair
        super().__init__(f"simulation already running for {sorted(pair)}")


# 진행 중인 (persona_a_id, persona_b_id) 조합. 순서 없는 쌍이라 frozenset.
_IN_FLIGHT: set[frozenset[str]] = set()
_IN_FLIGHT_LOCK = asyncio.Lock()


# ══ 대본 정리 ══════════════════════════════════════════════


def normalize_script(lines: list[ScriptLine], turns: int) -> list[Turn]:
    """LLM 대본 → Transcript.turns.

    모델이 순서를 어기거나 줄 수를 틀리는 일이 실제로 생긴다. 여기서 바로잡는다:
      - 같은 화자가 연달아 말하면 한 줄로 합친다 (교대 유지)
      - a 가 먼저 말하지 않았으면 앞의 b 줄은 버린다
      - 요청 턴 수(왕복)보다 길면 자르고, 짧으면 경고만 남긴다 (있는 대화로 리포트를 쓰는 편이 낫다)
    """
    merged: list[ScriptLine] = []
    for line in lines:
        text = line.text.strip()
        if not text:
            continue
        if merged and merged[-1].speaker == line.speaker:
            merged[-1] = ScriptLine(speaker=line.speaker, text=f"{merged[-1].text} {text}")
        else:
            merged.append(ScriptLine(speaker=line.speaker, text=text))

    while merged and merged[0].speaker != "a":
        merged.pop(0)

    want = turns * 2
    if len(merged) > want:
        merged = merged[:want]
    elif len(merged) < want:
        logger.warning("script has %d lines, wanted %d", len(merged), want)

    return [Turn(index=i, speaker=line.speaker, text=line.text[:1000]) for i, line in enumerate(merged)]


# ══ 서비스 ═════════════════════════════════════════════════


class SimulationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = SimulationRepository(db)
        self.agent = SimulationAgent()

    async def _load(self, who: str, ref: PersonaRef) -> LoadedPersona:
        loaded = await load_persona(self.db, ref)
        if loaded is None:
            raise PersonaNotFound(who, ref)
        return loaded

    async def run(self, req: SimulationRequest) -> SimulationResponse:
        me = await self._load("me", req.me_ref())
        partner = await self._load("partner", req.partner_ref())
        pa, pb = me.response, partner.response
        # 읽기만 한 트랜잭션을 닫아 커넥션을 풀에 돌려준다. 안 그러면 LLM 을 기다리는 최대 120초 동안
        # 커넥션을 붙잡고 있어서, 동시 요청이 풀 크기(기본 15)를 넘으면 다른 요청까지 막힌다.
        # 저장은 아래에서 새 트랜잭션으로 하고 커밋은 여전히 라우트가 한다.
        await self.db.commit()

        # 같은 페르소나 조합이 이미 처리 중이면 더블클릭·재시도로 보고 LLM 을 또 부르지 않는다
        pair = frozenset((me.record.id, partner.record.id))
        async with _IN_FLIGHT_LOCK:
            if pair in _IN_FLIGHT:
                logger.info("simulation refused: pair already running %s", sorted(pair))
                raise SimulationAlreadyRunning(pair)
            _IN_FLIGHT.add(pair)
        started = time.monotonic()
        try:
            result = await self._run_locked(req, me, partner, pa, pb)
        except SimulationFailed as e:
            logger.warning(
                "simulation failed: reason=%s turns=%d elapsed=%.1fs error=%s",
                e.reason,
                req.turns,
                time.monotonic() - started,
                e,
            )
            raise
        finally:
            async with _IN_FLIGHT_LOCK:
                _IN_FLIGHT.discard(pair)
        logger.info(
            "simulation ok: id=%s turns=%d lines=%d elapsed=%.1fs",
            result.simulation_id,
            req.turns,
            len(result.transcript),
            time.monotonic() - started,
        )
        return result

    async def _run_locked(
        self,
        req: SimulationRequest,
        me: LoadedPersona,
        partner: LoadedPersona,
        pa: PersonaResponse,
        pb: PersonaResponse,
    ) -> SimulationResponse:
        # 규칙 점수를 먼저 — LLM 에게 "설명할 재료"로 준다. ideal 은 아직 None
        dims, area_scores, _ = score_layer(pa, pb)

        # LLM 1회: 대본 + 서술
        script = await self.agent.run(
            persona_a=pa,
            persona_b=pb,
            name_a=me.nickname,
            name_b=partner.nickname,
            turns=req.turns,
            area_scores=area_scores,
            dim_scores={d.dimension: d.score for d in dims},
            trace_metadata=build_langfuse_metadata(
                feature="simulation",
                operation="run",
                user_id=me.record.user_id,
                requestedTurns=req.turns,
            ),
            db=self.db,
            user_key=me.record.user_id,
        )
        turns = normalize_script(script.transcript, req.turns)
        if len(turns) < 2:
            raise SimulationFailed("script too short after normalization", reason="script_too_short")

        # 하이라이트가 잘려 나간 줄을 가리키면 버린다
        narrative = script.report
        narrative.highlights = [h for h in narrative.highlights if 0 <= h.turn_index < len(turns)]

        record = await self.repo.save(
            persona_a_id=me.record.id,
            persona_b_id=partner.record.id,
            user_id_a=me.record.user_id,
            user_id_b=partner.record.user_id,
            nickname_a=me.nickname,
            nickname_b=partner.nickname,
            turns=req.turns,
            transcript=[],  # id 가 필요해서 리포트보다 먼저 만든다. 아래에서 채움
            report={},
            narrative_source="llm",
        )
        transcript = Transcript(simulation_id=record.id, turns=turns)
        report = assemble_report(
            ReportInput(
                persona_a=pa,
                persona_b=pb,
                transcript=transcript,
                nickname_a=me.nickname,
                nickname_b=partner.nickname,
            ),
            narrative,
            "llm",
        )
        report.validationResult = script.validation
        record.transcript = [t.model_dump() for t in turns]
        record.report = report.model_dump(mode="json")
        await self.db.flush()

        return self._to_response(record, me.brief, partner.brief)

    # ── 조회 ──────────────────────────────────────────────

    async def get(self, simulation_id: str) -> SimulationResponse:
        record = await self.repo.get(simulation_id)
        if record is None:
            raise SimulationNotFound(simulation_id)
        me, partner = await self._briefs(record)
        return self._to_response(record, me, partner)

    async def get_report(self, simulation_id: str) -> MatchingReport:
        record = await self.repo.get(simulation_id)
        if record is None:
            raise SimulationNotFound(simulation_id)
        return MatchingReport.model_validate(record.report)

    async def list_for(self, ref: PersonaRef) -> list[SimulationSummary]:
        if ref.user_id:
            records = await self.repo.list_for_user(ref.user_id)
        else:
            loaded = await self._load("me", ref)
            records = await self.repo.list_for_persona(loaded.record.id)
        out = []
        for r in records:
            me, partner = await self._briefs(r)
            overall = r.report.get("overall", {})
            score = int(overall.get("score", 0))
            out.append(
                SimulationSummary(
                    simulation_id=r.id,
                    me=me,
                    partner=partner,
                    turns=r.turns,
                    overall_score=score,
                    grade=grade_of(score),
                    grade_label=GRADE_LABEL[grade_of(score)],
                    headline=overall.get("headline", ""),
                    created_at=r.created_at,
                )
            )
        return out

    async def _briefs(self, record: SimulationRecord) -> tuple[PersonaBrief, PersonaBrief]:
        """저장 당시의 페르소나 행으로. 없어졌으면(지웠으면) 저장해 둔 닉네임만으로 만든다."""

        async def brief(persona_id: str, user_id: str | None, nickname: str) -> PersonaBrief:
            loaded = await load_persona(self.db, PersonaRef(persona_id=persona_id))
            if loaded is None:
                return PersonaBrief(persona_id=persona_id, user_id=user_id, nickname=nickname)
            return loaded.brief

        return (
            await brief(record.persona_a_id, record.user_id_a, record.nickname_a),
            await brief(record.persona_b_id, record.user_id_b, record.nickname_b),
        )

    @staticmethod
    def _to_response(record: SimulationRecord, me: PersonaBrief, partner: PersonaBrief) -> SimulationResponse:
        return SimulationResponse(
            simulation_id=record.id,
            validationResult=MatchingReport.model_validate(record.report).validationResult,
            me=me,
            partner=partner,
            turns=record.turns,
            transcript=[Turn.model_validate(t) for t in record.transcript],
            report=MatchingReport.model_validate(record.report),
            created_at=record.created_at,
        )
