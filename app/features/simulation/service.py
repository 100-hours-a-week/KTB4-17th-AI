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

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.features.persona.lookup import LoadedPersona, load_persona
from app.features.persona.schemas import PersonaBrief, PersonaRef

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
        )
        turns = normalize_script(script.transcript, req.turns)
        if len(turns) < 2:
            raise SimulationFailed("script too short after normalization")

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
            me=me,
            partner=partner,
            turns=record.turns,
            transcript=[Turn.model_validate(t) for t in record.transcript],
            report=MatchingReport.model_validate(record.report),
            created_at=record.created_at,
        )
