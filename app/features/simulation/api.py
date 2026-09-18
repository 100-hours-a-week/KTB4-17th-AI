"""라우터. 검증과 상태코드만 담당하고 로직은 report/service 로 넘긴다.

시뮬레이션 실행(SSE)과 페르소나 로딩은 아직 없다. 리포트 형식만 먼저 고정한다:
  - POST /report/preview : 페르소나 둘 + 대화록을 직접 넣어 리포트를 받는다. 프론트·플레이그라운드용.
  - GET  /{id}/report    : 저장된 시뮬레이션의 리포트. SSE 가 붙은 뒤 구현.

SSE 가 생기면 마지막 이벤트로 같은 MatchingReport 를 보낸다:
  event: report
  data: <MatchingReport JSON>
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .agents import ReportAgent
from .report import build_report
from .schemas import MatchingReport, ReportInput

router = APIRouter(prefix="/v1/simulation", tags=["simulation"])


@router.post("/report/preview", response_model=MatchingReport)
async def preview_report(
    inp: ReportInput,
    use_llm: bool = Query(default=True, description="False 면 LLM 없이 템플릿 서술로"),
) -> MatchingReport:
    return await build_report(inp, ReportAgent() if use_llm else None)


@router.get("/{simulation_id}/report", response_model=MatchingReport)
async def get_report(simulation_id: str) -> MatchingReport:
    raise HTTPException(status_code=501, detail="시뮬레이션 저장소가 아직 없음. /report/preview 를 쓰세요.")
