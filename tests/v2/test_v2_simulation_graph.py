"""하네스 그래프 경로 — 가짜 LLM 으로 6경로 + 호출 수 상한 + trace 원문 금지를 확인한다.

1. 바로 통과                     2. 국소 오류 → repair → 통과
3. 구조 오류 → regenerate         4. 예산 소진 → fail / accept_with_warning
5. 심사 오류 → judge_skipped      6. 규칙 실패 → repair → 전체 심사가 반드시 실행됨
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.core.config import get_settings
from app.v2.simulation.graph import run_harness
from app.v2.simulation.llm import SimLLMError
from tests.v2.sim_fixtures import (
    FakeLLM,
    ctx,
    fact,
    good_script,
    judge_flag,
    judge_pass,
    patch,
    report_call,
    script_call,
    vec,
)

TURNS = 10


def _run(llm, *, facts_a=None, facts_b=None, judge_llm=None, clock=None, deadline_s=None, stages=None):
    async def on_stage(name):
        if stages is not None:
            stages.append(name)

    kwargs = {}
    if clock is not None:
        kwargs["clock"] = clock
    return asyncio.run(
        run_harness(
            ctx(),
            facts_a or [],
            facts_b or [],
            TURNS,
            job_id="j1",
            llm=llm,
            judge_llm=judge_llm,
            on_stage=on_stage,
            deadline_s=deadline_s,
            **kwargs,
        )
    )


def _bad(i: int, **kw):
    s = good_script(TURNS)
    s[i] = s[i].model_copy(update=kw)
    return s


SELF_ADDRESS = {"text": "민수님은 주말에 뭐 하세요?"}  # 2번 줄 화자 a(민수)의 자기 호칭


def _script_calls(llm: FakeLLM) -> int:
    return sum(llm.count(k) for k in ("generate", "regenerate", "repair", "judge"))


def test_path1_pass_directly():
    stages: list[str] = []
    llm = FakeLLM({"generate": [script_call(good_script())], "judge": [judge_pass()], "report": [report_call()]})
    res = _run(llm, stages=stages)
    assert res.status == "succeeded"
    assert [c["name"] for c in llm.calls] == ["simulation-v2-generate", "simulation-v2-judge", "simulation-v2-report"]
    assert res.llm_calls == 3
    assert res.report.narrative_source == "llm"
    assert stages == ["topics", "generating", "verifying", "reporting"]


def test_path2_local_error_repair_then_pass():
    llm = FakeLLM(
        {
            "generate": [script_call(_bad(2, **SELF_ADDRESS))],
            "repair": [patch(2, "지은님은 주말에 뭐 하세요?")],
            "judge": [judge_pass()],
            "report": [report_call()],
        }
    )
    res = _run(llm)
    assert res.status == "succeeded"
    assert llm.count("repair") == 1 and llm.count("judge") == 1
    assert res.script[2].text == "지은님은 주말에 뭐 하세요?"


def test_path3_structural_error_regenerates_with_correction():
    short = good_script()[:-3]
    llm = FakeLLM(
        {
            "generate": [script_call(short)],
            "regenerate": [script_call(good_script())],
            "judge": [judge_pass()],
            "report": [report_call()],
        }
    )
    res = _run(llm)
    assert res.status == "succeeded"
    assert llm.count("generate") == 1 and llm.count("regenerate") == 1
    regen = next(c for c in llm.calls if c["name"] == "simulation-v2-regenerate")
    assert "이전 대본의 문제" in regen["user"] and "format" in regen["user"]


def test_path4_budget_exhausted_speaker_mixup_fails():
    bad = _bad(2, **SELF_ADDRESS)
    llm = FakeLLM(
        {
            "generate": [script_call(bad)],
            "repair": [patch(2, SELF_ADDRESS["text"] + " ㅎㅎ")],  # 계속 같은 실수
            "regenerate": [script_call(bad)],
            "judge": [judge_pass()],
            "report": [report_call()],
        }
    )
    res = _run(llm)
    assert res.status == "failed"
    assert res.reason == "speaker_mixup_unresolved"
    assert llm.count("report") == 0
    assert _script_calls(llm) <= get_settings().simulation_v2_script_budget


def test_path4_budget_exhausted_soft_issue_accepts_with_warning():
    llm = FakeLLM(
        {
            "generate": [script_call(good_script())],
            "judge": [judge_flag(5, "judge_unresponsive")],  # 고쳐도 계속 지적
            "repair": [patch(5, "그렇군요, 저는 요즘 에세이를 자주 읽어요. 혹시 좋아하는 작가 있으세요?")],
            "report": [report_call()],
        }
    )
    res = _run(llm)
    assert res.status == "succeeded"
    assert "judge_unresponsive" in res.quality_warnings
    assert _script_calls(llm) <= get_settings().simulation_v2_script_budget


def test_path5_judge_error_is_skipped():
    llm = FakeLLM(
        {
            "generate": [script_call(good_script())],
            "judge": [SimLLMError("down", reason="upstream_error")],
            "report": [report_call()],
        }
    )
    res = _run(llm)
    assert res.status == "succeeded"
    assert any(e.node == "judge" and e.note == "judge_skipped" for e in res.trace)


def test_path6_rule_fail_then_repair_runs_full_judge():
    llm = FakeLLM(
        {
            "generate": [script_call(_bad(2, **SELF_ADDRESS))],
            "repair": [patch(2, "지은님은 주말에 뭐 하세요?")],
            "judge": [judge_pass()],
            "report": [report_call()],
        }
    )
    res = _run(llm)
    assert res.status == "succeeded"
    judge_call = next(c for c in llm.calls if c["name"] == "simulation-v2-judge")
    assert "전체 대본" in judge_call["user"]
    assert any(e.node == "judge" and e.note == "judge_scope=full" for e in res.trace)


def test_judge_flag_then_repair_uses_focus_judge():
    llm = FakeLLM(
        {
            "generate": [script_call(good_script())],
            "judge": [judge_flag(7), judge_pass()],
            "repair": [patch(7, "아침 요가라니 부지런하시네요. 저는 아침엔 산책을 해요.")],
            "report": [report_call()],
        }
    )
    res = _run(llm)
    assert res.status == "succeeded"
    judges = [c for c in llm.calls if c["name"] == "simulation-v2-judge"]
    assert len(judges) == 2
    assert "전체 대본" in judges[0]["user"] and "전체 대본" not in judges[1]["user"]
    notes = [e.note for e in res.trace if e.node == "judge"]
    assert notes == ["judge_scope=full", "judge_scope=focus"]


def test_generate_llm_error_regenerates_then_fails_with_reason():
    llm = FakeLLM(
        {
            "generate": [SimLLMError("t", reason="timeout")],
            "regenerate": [SimLLMError("t", reason="timeout")],
        }
    )
    res = _run(llm)
    assert res.status == "failed" and res.reason == "timeout"
    assert llm.count("generate") == 1 and llm.count("regenerate") == 1


def test_no_retry_when_time_is_short():
    t = [0.0]

    def clock():
        return t[0]

    async def slow_generate(**kw):
        t[0] += 80.0  # 생성에 80초 → 남은 10초 < 25초
        return script_call(_bad(2, **SELF_ADDRESS))

    llm = FakeLLM({"report": [report_call()]})

    async def router(**kw):
        if kw["name"] == "simulation-v2-generate":
            llm.calls.append({"name": kw["name"]})
            return await slow_generate(**kw)
        return await llm(**kw)

    res = _run(router, clock=clock, deadline_s=90)
    assert res.status == "failed" and res.reason == "speaker_mixup_unresolved"
    assert llm.count("repair") == 0


def test_report_failure_falls_back_to_template():
    llm = FakeLLM(
        {
            "generate": [script_call(good_script())],
            "judge": [judge_pass()],
            "report": [SimLLMError("x")],
        }
    )
    res = _run(llm)
    assert res.status == "succeeded"
    assert res.report.narrative_source == "template"
    assert llm.count("report") == get_settings().simulation_v2_report_budget


def test_report_perspective_swap_retries_once_then_template():
    # b 의 관심사(독서 모임)를 a 이름 뒤에 붙인 리포트
    swapped = report_call(headline="민수님은 독서 모임을 좋아해요")
    llm = FakeLLM({"generate": [script_call(good_script())], "judge": [judge_pass()], "report": [swapped]})
    res = _run(llm)
    assert llm.count("report") == 2
    assert res.report.narrative_source == "template"


def test_topics_used_and_trace_has_no_raw_text():
    fa = [fact(1, "주말마다 산에 간다", vec(1.0))]
    fb = [fact(11, "트레킹 동호회 활동", vec(0.95, 0.1))]
    script = good_script()
    script[2] = script[2].model_copy(update={"fact_refs": ["A-f1"]})
    script[3] = script[3].model_copy(update={"fact_refs": ["B-f1", "A-f1"]})
    llm = FakeLLM({"generate": [script_call(script)], "judge": [judge_pass()], "report": [report_call()]})
    res = _run(llm, facts_a=fa, facts_b=fb)
    assert res.status == "succeeded" and res.mode == "shared"
    assert [t.ref for t in res.topics_used] == ["A-f1", "B-f1"]
    gen = next(c for c in llm.calls if c["name"] == "simulation-v2-generate")
    assert "A-f1" in gen["user"] and "주말마다 산에 간다" in gen["user"]
    dumped = json.dumps([e.model_dump() for e in res.trace], ensure_ascii=False)
    for line in script:
        assert line.text not in dumped
    assert "주말마다 산에 간다" not in dumped


@pytest.mark.parametrize("seed", range(6))
def test_call_caps_never_exceeded(seed):
    """어떤 실패 조합이든 대본 단계 ≤ 5, 리포트 ≤ 2."""
    bad_variants = [
        script_call(_bad(2, **SELF_ADDRESS)),
        script_call(good_script()[:-1]),
        SimLLMError("x", reason="invalid_json"),
    ]
    llm = FakeLLM(
        {
            "generate": [bad_variants[seed % 3]],
            "regenerate": [bad_variants[(seed + 1) % 3]],
            "repair": [patch(2, SELF_ADDRESS["text"] + "!")],
            "judge": [judge_flag(4) if seed % 2 else judge_pass()],
            "report": [SimLLMError("x")],
        }
    )
    _run(llm)
    s = get_settings()
    assert _script_calls(llm) <= s.simulation_v2_script_budget
    assert llm.count("regenerate") <= s.simulation_v2_regenerate_budget
    assert llm.count("repair") <= s.simulation_v2_repair_budget
    assert llm.count("report") <= s.simulation_v2_report_budget
