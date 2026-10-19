"""하네스 — 노드 연결·라우팅·예산. 흐름은 코드가 정하고 LLM 은 각 노드의 출력만 만든다.

  plan_topics ─▶ generate ─▶ verify ──pass──────────────▶ write_report ─▶ END
                    ▲          ├─ local ─▶ repair ─▶ verify
                    └──────────┴─ structural ─ (regenerate)
                               └─ 예산 소진 ─▶ exhausted ─▶ write_report(accept_with_warning) | END(failed)

예산(하드 상한): 대본 단계 LLM 호출(generate·regenerate·repair·심사) settings.simulation_v2_script_budget,
그중 regenerate ≤ simulation_v2_regenerate_budget, repair ≤ simulation_v2_repair_budget,
리포트 단계 ≤ simulation_v2_report_budget. repair·regenerate 는 남은 대본 예산 ≥ 2 이고
남은 시간 ≥ simulation_v2_min_remaining_s 일 때만 들어간다 (고친 뒤 심사 1회가 따라와야 의미가 있다).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, Literal

from langgraph.graph import END, StateGraph

from app.core.config import get_settings

from . import generator, repair, report, verifier
from .llm import SimLLMError
from .schemas import (
    FAIL_TYPES,
    SPEAKER_MIXUP_TYPES,
    Budget,
    FactRow,
    HarnessResult,
    Issue,
    LLMCaller,
    SimContext,
    SimV2State,
    StageCallback,
    TopicFact,
    TraceEvent,
    TraceIssue,
)
from .topics import match_topics

logger = logging.getLogger(__name__)

_MIN_JUDGE_S = 5.0  # 이보다 시간이 적게 남으면 심사를 건너뛰고 규칙 결과만 믿는다
_MIN_REPORT_S = 3.0  # 이보다 적게 남으면 리포트는 템플릿으로


def _trace_issues(issues: list[Issue]) -> list[TraceIssue]:
    return [TraceIssue(type=x.type, i=x.i) for x in issues]


def _correction(issues: list[Issue]) -> str:
    rows = []
    for x in issues[:12]:
        where = f"{x.i}번 줄" if x.i is not None else "대본 전체"
        rows.append(f"- {where}: {x.type} — {x.evidence}")
    return "\n".join(rows)


def _fail_reason(issues: list[Issue]) -> str:
    types = {x.type for x in issues}
    if types & SPEAKER_MIXUP_TYPES:
        return "speaker_mixup_unresolved"
    if "format" in types:
        return "invalid_script"
    return "fabrication_unresolved"


def build_graph(
    *,
    llm: LLMCaller,
    judge_llm: LLMCaller,
    on_stage: StageCallback | None,
    clock: Callable[[], float],
    metadata: dict[str, Any] | None,
):
    s = get_settings()

    def remaining(st: SimV2State) -> float:
        return st.attempts.deadline - clock()

    async def stage(name: str) -> None:
        if on_stage is None:
            return
        try:
            await on_stage(name)
        except Exception:
            logger.warning("simulation v2 stage callback failed (%s)", name, exc_info=True)

    def can_retry(st: SimV2State) -> bool:
        left = s.simulation_v2_script_budget - st.attempts.script_calls
        return left >= 2 and remaining(st) >= s.simulation_v2_min_remaining_s

    # ── 노드 ──────────────────────────────────────────────

    async def plan_topics(st: SimV2State) -> dict:
        await stage("topics")
        started = clock()
        plan = match_topics(st.facts_a, st.facts_b, turns=st.turns)
        # fact_ids 는 소재로 계획된 fact 만 (소재 활용률 지표용). 노출 fact 수는 note 에
        ids = [f.fact_id for it in plan.items for f in (it.a_fact, it.b_fact) if f is not None]
        groups = "|".join(
            "+".join(str(f.fact_id) for f in (it.a_fact, it.b_fact) if f is not None) for it in plan.items
        )
        note = f"mode={plan.mode} exposed={len(plan.facts_a)}+{len(plan.facts_b)} topics={groups}"
        ev = TraceEvent(node="plan_topics", ms=int((clock() - started) * 1000), fact_ids=ids, note=note)
        return {"topics": plan, "trace": [*st.trace, ev]}

    async def generate(st: SimV2State) -> dict:
        await stage("generating")
        is_regen = st.attempts.script_calls > 0
        attempts = st.attempts.model_copy(
            update={
                "script_calls": st.attempts.script_calls + 1,
                "regenerate": st.attempts.regenerate + (1 if is_regen else 0),
            }
        )
        correction = _correction(st.issues) if is_regen and st.issues else None
        node = "regenerate" if is_regen else "generate"
        started = clock()
        ev = TraceEvent(node=node)
        timeout = max(min(s.simulation_v2_generate_timeout_s, remaining(st)), 0.1)
        try:
            script, res = await generator.generate(
                llm, st.ctx, st.topics, st.turns, correction=correction, timeout=timeout, metadata=metadata
            )
            ev.prompt_tokens, ev.completion_tokens = res.prompt_tokens, res.completion_tokens
            last_err = None
        except SimLLMError as e:
            logger.warning("simulation v2 %s failed: reason=%s", node, e.reason)
            script, last_err = [], e.reason
            ev.note = f"llm_error={e.reason}"
        ev.ms = int((clock() - started) * 1000)
        return {
            "script": script,
            "attempts": attempts,
            "judged_full": False,
            "patched_lines": [],
            "issues": [],
            "last_llm_error": last_err,
            "trace": [*st.trace, ev],
        }

    async def verify(st: SimV2State) -> dict:
        await stage("verifying")
        started = clock()
        issues, warns = verifier.rule_check(st.script, st.ctx, st.topics, st.turns)
        warnings = list(dict.fromkeys([*st.warnings, *warns]))
        ev = TraceEvent(node="rule_check", ms=int((clock() - started) * 1000), issues=_trace_issues(issues))
        trace = [*st.trace, ev]
        if issues:
            # 규칙에서 걸리면 이번 시도의 심사는 건너뛴다
            return {"issues": issues, "warnings": warnings, "trace": trace}

        # 규칙 통과 → LLM 심사 (예산·시간이 있을 때만)
        if st.attempts.script_calls >= s.simulation_v2_script_budget or remaining(st) < _MIN_JUDGE_S:
            trace.append(TraceEvent(node="judge", note="judge_unaffordable"))
            return {"issues": [], "warnings": warnings, "trace": trace, "judge_skipped": True}

        full = not st.judged_full
        focus = None if full else set(st.patched_lines)
        attempts = st.attempts.model_copy(update={"script_calls": st.attempts.script_calls + 1})
        started = clock()
        result = await verifier.judge(
            judge_llm,
            st.script,
            st.ctx,
            st.topics,
            focus=focus,
            timeout=max(min(s.simulation_v2_judge_timeout_s, remaining(st) - 1), 0.1),
            model=s.simulation_v2_judge_model,
            metadata=metadata,
        )
        jev = TraceEvent(
            node="judge",
            ms=int((clock() - started) * 1000),
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            issues=_trace_issues(result.issues),
            note="judge_skipped" if result.skipped else ("judge_scope=full" if full else "judge_scope=focus"),
        )
        trace.append(jev)
        return {
            "issues": result.issues,
            "warnings": warnings,
            "trace": trace,
            "attempts": attempts,
            # 심사가 실패(skipped)했으면 전체 심사를 받은 것으로 치지 않는다
            "judged_full": st.judged_full or (full and not result.skipped),
            "judge_skipped": st.judge_skipped or result.skipped,
        }

    async def do_repair(st: SimV2State) -> dict:
        await stage("repairing")
        attempts = st.attempts.model_copy(
            update={"script_calls": st.attempts.script_calls + 1, "repair": st.attempts.repair + 1}
        )
        started = clock()
        ev = TraceEvent(node="repair", issues=_trace_issues(st.issues))
        timeout = max(min(s.simulation_v2_repair_timeout_s, remaining(st)), 0.1)
        try:
            script, patched, res = await repair.repair(
                llm, st.script, st.issues, st.ctx, st.topics, timeout=timeout, metadata=metadata
            )
            ev.prompt_tokens, ev.completion_tokens = res.prompt_tokens, res.completion_tokens
        except SimLLMError as e:
            logger.warning("simulation v2 repair failed: reason=%s", e.reason)
            script, patched = st.script, set()
            ev.note = f"llm_error={e.reason}"
        ev.ms = int((clock() - started) * 1000)
        return {
            "script": script,
            "patched_lines": sorted(patched),
            "attempts": attempts,
            "trace": [*st.trace, ev],
        }

    async def exhausted(st: SimV2State) -> dict:
        remaining_issues = st.issues
        trace = [*st.trace, TraceEvent(node="exhausted", issues=_trace_issues(remaining_issues))]
        if not st.script and st.last_llm_error:
            reason = "timeout" if st.last_llm_error == "timeout" else "llm_error"
            return {"outcome": "failed", "reason": reason, "trace": trace}
        if any(x.type in FAIL_TYPES for x in remaining_issues):
            # 실패 대본은 저장하지 않으므로 원인 분석용으로 이슈 종류·줄 번호만 남긴다 (문장 원문 없음)
            logger.info(
                "simulation v2 job %s failed: calls=%d issues=%s",
                st.job_id,
                st.attempts.script_calls,
                [(x.type, x.i) for x in remaining_issues],
            )
            return {"outcome": "failed", "reason": _fail_reason(remaining_issues), "trace": trace}
        warnings = list(dict.fromkeys([*st.warnings, *(x.type for x in remaining_issues)]))
        return {"outcome": "accepted_with_warning", "warnings": warnings, "trace": trace}

    async def write_report(st: SimV2State) -> dict:
        await stage("reporting")
        budget = s.simulation_v2_report_budget if remaining(st) >= _MIN_REPORT_S else 0
        timeout = max(min(s.simulation_v2_report_timeout_s, remaining(st)), 0.1)
        rep, calls, events = await report.write_report(
            llm, st.script, st.ctx, st.topics, budget=budget, timeout=timeout, metadata=metadata, clock=clock
        )
        attempts = st.attempts.model_copy(update={"report_calls": st.attempts.report_calls + calls})
        outcome = st.outcome if st.outcome != "running" else "succeeded"
        return {"report": rep, "attempts": attempts, "outcome": outcome, "trace": [*st.trace, *events]}

    # ── 라우팅 ────────────────────────────────────────────

    def after_verify(st: SimV2State) -> Literal["write_report", "repair", "generate", "exhausted"]:
        decision = verifier.route(st.issues, len(st.script) or st.turns * 2)
        if decision == "pass":
            return "write_report"
        if not can_retry(st):
            return "exhausted"
        has_structural = any(x.severity == "structural" for x in st.issues)
        regen_left = st.attempts.regenerate < s.simulation_v2_regenerate_budget
        repair_left = st.attempts.repair < s.simulation_v2_repair_budget
        if decision == "regenerate":
            if regen_left:
                return "generate"
            # 재생성 예산이 없으면 구조 오류가 아닌 경우에 한해 국소 수정으로 버틴다
            return "repair" if repair_left and not has_structural else "exhausted"
        if repair_left:
            return "repair"
        return "generate" if regen_left else "exhausted"

    def after_exhausted(st: SimV2State) -> Literal["write_report", "__end__"]:
        return "write_report" if st.outcome == "accepted_with_warning" else END

    g = StateGraph(SimV2State)
    g.add_node("plan_topics", plan_topics)
    g.add_node("generate", generate)
    g.add_node("verify", verify)
    g.add_node("repair", do_repair)
    g.add_node("exhausted", exhausted)
    g.add_node("write_report", write_report)
    g.set_entry_point("plan_topics")
    g.add_edge("plan_topics", "generate")
    g.add_edge("generate", "verify")
    g.add_conditional_edges("verify", after_verify)
    g.add_edge("repair", "verify")
    g.add_conditional_edges("exhausted", after_exhausted)
    g.add_edge("write_report", END)
    return g.compile()


def _topics_used(st: SimV2State) -> list[TopicFact]:
    if st.topics is None:
        return []
    by_ref = st.topics.fact_by_ref()
    out: list[TopicFact] = []
    seen: set[str] = set()
    for line in st.script:
        for ref in line.fact_refs:
            if ref in by_ref and ref not in seen:
                seen.add(ref)
                out.append(by_ref[ref])
    return out


async def run_harness(
    ctx: SimContext,
    facts_a: list[FactRow],
    facts_b: list[FactRow],
    turns: int,
    *,
    job_id: str,
    llm: LLMCaller,
    judge_llm: LLMCaller | None = None,
    on_stage: StageCallback | None = None,
    clock: Callable[[], float] = time.monotonic,
    deadline_s: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> HarnessResult:
    s = get_settings()
    total = s.simulation_v2_job_timeout_s if deadline_s is None else deadline_s
    state = SimV2State(
        job_id=job_id,
        turns=turns,
        ctx=ctx,
        facts_a=facts_a,
        facts_b=facts_b,
        attempts=Budget(deadline=clock() + total),
    )
    graph = build_graph(llm=llm, judge_llm=judge_llm or llm, on_stage=on_stage, clock=clock, metadata=metadata)
    raw = await graph.ainvoke(state, config={"recursion_limit": 50})
    final = raw if isinstance(raw, SimV2State) else SimV2State.model_validate(raw)

    calls = final.attempts.script_calls + final.attempts.report_calls
    mode = final.topics.mode if final.topics else "persona_only"
    if final.outcome == "failed" or final.report is None:
        return HarnessResult(
            status="failed",
            reason=final.reason or "invalid_script",
            mode=mode,
            quality_warnings=final.warnings,
            trace=final.trace,
            llm_calls=calls,
        )
    return HarnessResult(
        status="succeeded",
        mode=mode,
        script=final.script,
        report=final.report,
        topics_used=_topics_used(final),
        quality_warnings=final.warnings,
        trace=final.trace,
        llm_calls=calls,
    )
