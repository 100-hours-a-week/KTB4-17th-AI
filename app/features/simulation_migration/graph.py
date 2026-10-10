from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Literal

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from app.features.persona.schemas import PersonaResponse
from app.features.simulation_migration.order import closing_hint_for, next_node
from app.features.simulation_migration.tools import speak_as_a, speak_as_b


class SimulationGraphState(BaseModel):
    """시뮬레이션 그래프 실행의 메모리 상태.

    DB 세션, 이벤트 큐, asyncio 객체는 상태에 두지 않는다.
    """

    run_id: str = ""
    attempt: int = 1
    persona_a: PersonaResponse
    persona_b: PersonaResponse
    nickname_a: str
    nickname_b: str
    turns: int
    transcript: list[tuple[Literal["a", "b"], str]] = Field(default_factory=list)
    error: str | None = None
    report: Any | None = None
    validation: Any | None = None


def route_next(state: SimulationGraphState) -> str:
    """order.next_node를 사용하여 다음 노드를 결정하는 라우팅 함수."""
    transcript = state.transcript if hasattr(state, "transcript") else state["transcript"]
    turns = state.turns if hasattr(state, "turns") else state["turns"]
    error = state.error if hasattr(state, "error") else state.get("error")
    return next_node(len(transcript), turns, error)


def _extract_partner_attributes(speaker_persona: Any, partner_persona: Any) -> list[str]:
    """화자 속성과 겹치지 않는 상대방의 속성 목록을 추출한다.

    화자 속성은 persona의 interests, routine, date_prefer이며,
    상대 속성은 상대의 같은 세 목록에서 화자 속성과 겹치는 값을 제외한 것이다.
    """
    own_attrs = [
        *getattr(speaker_persona, "interests", []),
        *getattr(speaker_persona, "routine", []),
        *getattr(speaker_persona, "date_prefer", []),
    ]
    partner_candidate = [
        *getattr(partner_persona, "interests", []),
        *getattr(partner_persona, "routine", []),
        *getattr(partner_persona, "date_prefer", []),
    ]
    return [item for item in partner_candidate if item not in own_attrs]


def create_simulation_graph(
    *,
    llm: Any = None,
    write_report: Callable | None = None,
) -> CompiledStateGraph:
    """시뮬레이션 LangGraph StateGraph를 구성하고 컴파일하여 반환한다.

    체크포인터와 interrupt는 사용하지 않으며, order.next_node만을 기반으로 라우팅한다.
    노드 이름은 speak_a, speak_b, report, stop이다.

    Args:
        llm: 화자 도구에 주입할 LLM 클라이언트 또는 함수.
        write_report: report 노드에서 실행할 리포트 작성 함수.

    Returns:
        컴파일된 StateGraph 인스턴스.
    """
    workflow = StateGraph(SimulationGraphState)

    async def speak_a_node(state: SimulationGraphState) -> dict[str, Any]:
        transcript = state.transcript if hasattr(state, "transcript") else state["transcript"]
        turns = state.turns if hasattr(state, "turns") else state["turns"]
        idx = len(transcript)
        hint = closing_hint_for(idx, turns)

        p_a = state.persona_a if hasattr(state, "persona_a") else state["persona_a"]
        p_b = state.persona_b if hasattr(state, "persona_b") else state["persona_b"]
        nick_a = state.nickname_a if hasattr(state, "nickname_a") else state["nickname_a"]
        nick_b = state.nickname_b if hasattr(state, "nickname_b") else state["nickname_b"]

        partner_attrs = _extract_partner_attributes(p_a, p_b)

        res = await speak_as_a(
            persona=p_a,
            nickname_self=nick_a,
            nickname_other=nick_b,
            transcript=transcript,
            closing_hint=hint,
            llm=llm,
            partner_attributes=partner_attrs,
            index=idx,
            turns=turns,
        )

        if res.text is not None:
            return {
                "transcript": transcript + [("a", res.text)],
                "validation": res.validation,
            }
        return {"error": res.reason or "speak_a_failed"}

    async def speak_b_node(state: SimulationGraphState) -> dict[str, Any]:
        transcript = state.transcript if hasattr(state, "transcript") else state["transcript"]
        turns = state.turns if hasattr(state, "turns") else state["turns"]
        idx = len(transcript)
        hint = closing_hint_for(idx, turns)

        p_a = state.persona_a if hasattr(state, "persona_a") else state["persona_a"]
        p_b = state.persona_b if hasattr(state, "persona_b") else state["persona_b"]
        nick_a = state.nickname_a if hasattr(state, "nickname_a") else state["nickname_a"]
        nick_b = state.nickname_b if hasattr(state, "nickname_b") else state["nickname_b"]

        partner_attrs = _extract_partner_attributes(p_b, p_a)

        res = await speak_as_b(
            persona=p_b,
            nickname_self=nick_b,
            nickname_other=nick_a,
            transcript=transcript,
            closing_hint=hint,
            llm=llm,
            partner_attributes=partner_attrs,
            index=idx,
            turns=turns,
        )

        if res.text is not None:
            return {
                "transcript": transcript + [("b", res.text)],
                "validation": res.validation,
            }
        return {"error": res.reason or "speak_b_failed"}

    async def report_node(state: SimulationGraphState) -> dict[str, Any]:
        if write_report is None:
            return {"report": {"status": "mock_report"}}
        if asyncio.iscoroutinefunction(write_report):
            rep = await write_report(state)
        else:
            rep = write_report(state)
        return {"report": rep}

    async def stop_node(state: SimulationGraphState) -> dict[str, Any]:
        return {}

    workflow.add_node("speak_a", speak_a_node)
    workflow.add_node("speak_b", speak_b_node)
    workflow.add_node("report", report_node)
    workflow.add_node("stop", stop_node)

    dest_map = {
        "speak_a": "speak_a",
        "speak_b": "speak_b",
        "report": "report",
        "stop": "stop",
    }

    workflow.set_conditional_entry_point(route_next, dest_map)
    workflow.add_conditional_edges("speak_a", route_next, dest_map)
    workflow.add_conditional_edges("speak_b", route_next, dest_map)
    workflow.add_edge("report", END)
    workflow.add_edge("stop", END)

    # 체크포인터 없이 컴파일
    return workflow.compile()
