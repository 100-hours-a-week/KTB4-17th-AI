from __future__ import annotations

import asyncio

import pytest

from app.features.persona.profile import describe
from app.features.persona.schemas import Narrative, PersonaResponse
from app.features.simulation_migration.graph import SimulationGraphState, create_simulation_graph
from app.features.simulation_migration.prompts import HINT_CLOSE, HINT_NO_NEW_QUESTION


class FakeLLM:
    """테스트용 가짜 LLM 클라이언트.

    전달된 messages를 순서대로 기록하고, 지정된 응답 목록(responses)을 차례대로 반환한다.
    응답 목록이 비어 있으면 default_response를 반환한다.
    """

    def __init__(self, default_response: str = '{"text": "안녕하세요 반갑습니다 ㅎㅎ"}') -> None:
        self.history: list[list[dict[str, str]]] = []
        self.responses: list[str | Exception] = []
        self.default_response = default_response

    async def __call__(self, messages: list[dict[str, str]]) -> str:
        self.history.append([dict(m) for m in messages])
        if self.responses:
            res = self.responses.pop(0)
            if isinstance(res, Exception):
                raise res
            return res
        return self.default_response


@pytest.fixture
def test_personas() -> tuple[PersonaResponse, PersonaResponse]:
    """비중첩 고유 문장을 가진 페르소나 A, B 픽스처."""
    p_a = PersonaResponse(
        persona_id="persona_alpha",
        scores={},
        interests=["산행", "등산"],
        routine=["새벽 조깅"],
        narrative=Narrative(
            headline="산이 좋은 등산가",
            body="알파인물만의산행",
            traits=["등산선호"],
        ),
    )
    p_b = PersonaResponse(
        persona_id="persona_beta",
        scores={},
        interests=["독서", "서점투어"],
        routine=["저녁 독서"],
        narrative=Narrative(
            headline="책이 좋은 문학인",
            body="베타인물만의서점",
            traits=["독서선호"],
        ),
    )
    return p_a, p_b


def test_turns_3_node_sequence(test_personas: tuple[PersonaResponse, PersonaResponse]) -> None:
    """turns=3일 때 노드 실행 열이 speak_a, speak_b, speak_a, speak_b, speak_a, speak_b, report 순서인지 검증."""

    async def _run() -> None:
        p_a, p_b = test_personas
        fake_llm = FakeLLM()
        report_called = False

        async def mock_report(state: SimulationGraphState) -> dict[str, str]:
            nonlocal report_called
            report_called = True
            return {"summary": "좋은 만남이었습니다"}

        graph = create_simulation_graph(llm=fake_llm, write_report=mock_report)

        initial_state = {
            "persona_a": p_a,
            "persona_b": p_b,
            "nickname_a": "알파",
            "nickname_b": "베타",
            "turns": 3,
            "transcript": [],
        }

        executed_nodes: list[str] = []
        async for event in graph.astream(initial_state):
            for node_name in event.keys():
                executed_nodes.append(node_name)

        expected = ["speak_a", "speak_b", "speak_a", "speak_b", "speak_a", "speak_b", "report"]
        assert executed_nodes == expected
        assert report_called is True
        assert len(fake_llm.history) == 6

    asyncio.run(_run())


def test_turns_15_node_sequence_and_no_combined_call(
    test_personas: tuple[PersonaResponse, PersonaResponse],
) -> None:
    """turns=15일 때 화자 호출 30회 후 report 1회 호출되며, 합친 JSON 호출은 0회인지 검증."""

    async def _run() -> None:
        p_a, p_b = test_personas
        fake_llm = FakeLLM()
        report_count = 0

        async def mock_report(state: SimulationGraphState) -> dict[str, str]:
            nonlocal report_count
            report_count += 1
            return {"status": "done"}

        graph = create_simulation_graph(llm=fake_llm, write_report=mock_report)

        initial_state = {
            "persona_a": p_a,
            "persona_b": p_b,
            "nickname_a": "알파",
            "nickname_b": "베타",
            "turns": 15,
            "transcript": [],
        }

        executed_nodes: list[str] = []
        async for event in graph.astream(initial_state):
            for node_name in event.keys():
                executed_nodes.append(node_name)

        # 화자 30회 (speak_a 15, speak_b 15 교대) + report 1회
        assert len([n for n in executed_nodes if n in ("speak_a", "speak_b")]) == 30
        assert executed_nodes[-1] == "report"
        assert report_count == 1
        assert len(fake_llm.history) == 30

        # 합친 script+report 호출 검증: 모든 LLM 호출 메시지에 report 관련 프롬프트 없음
        for call_messages in fake_llm.history:
            for m in call_messages:
                assert "ScriptOutput" not in m["content"]
                assert "MatchingReport" not in m["content"]

    asyncio.run(_run())


def test_profile_isolation_in_all_messages(test_personas: tuple[PersonaResponse, PersonaResponse]) -> None:
    """a의 모든 messages에 describe(b)의 비중첩 문장이 없고, b도 대칭이며 재생성에도 유지되는지 검증."""

    async def _run() -> None:
        p_a, p_b = test_personas
        desc_a = describe("알파", p_a)
        desc_b = describe("베타", p_b)
        assert "알파인물만의산행" in desc_a
        assert "베타인물만의서점" in desc_b

        fake_llm = FakeLLM()
        # 2번째 호출(speak_b)에서 self_addressed 위반 발생시켜 재생성 유도
        fake_llm.responses = [
            '{"text": "안녕하세요"}',  # 1: a
            '{"text": "베타님 안녕하세요"}',  # 2: b (self_addressed 위반 -> CLOSING-QUESTION/SELF-ADDRESS 재생성)
            '{"text": "반갑습니다 ㅎㅎ"}',  # 3: b 재생성
        ]

        graph = create_simulation_graph(llm=fake_llm, write_report=lambda s: {})

        initial_state = {
            "persona_a": p_a,
            "persona_b": p_b,
            "nickname_a": "알파",
            "nickname_b": "베타",
            "turns": 3,
            "transcript": [],
        }

        # 처음 두 노드만 실행
        step_count = 0
        async for _ in graph.astream(initial_state):
            step_count += 1
            if step_count >= 2:
                break

        # 화자 A 호출(1회차): B의 고유 문장 부재
        call_a = fake_llm.history[0]
        for m in call_a:
            assert "베타인물만의서점" not in m["content"]

        # 화자 B 호출(2회차 최초 + 3회차 재생성): A의 고유 문장 부재
        for call_b in fake_llm.history[1:3]:
            for m in call_b:
                assert "알파인물만의산행" not in m["content"]

    asyncio.run(_run())


def test_closing_hints_in_last_turns_and_regeneration(
    test_personas: tuple[PersonaResponse, PersonaResponse],
) -> None:
    """마지막 a에만 질문 금지, 마지막 b에만 마무리 문장이 있고, 재생성에도 유지되며 마지막 a에 마무리 문장이 없는지 검증."""

    async def _run() -> None:
        p_a, p_b = test_personas
        fake_llm = FakeLLM()

        # 마지막 a(5번째 대사, index 4)에서 1회 재생성 유도
        # 마지막 b(6번째 대사, index 5)에서 1회 재생성 유도
        fake_llm.responses = [
            '{"text": "대사 0"}',  # a0
            '{"text": "대사 1"}',  # b1
            '{"text": "대사 2"}',  # a2
            '{"text": "대사 3"}',  # b3
            '{"text": "알파님 대사 4"}',  # a4 (self-addressed 위반)
            '{"text": "대사 4 재작성"}',  # a4 재생성
            '{"text": "다음에 또 봐요?"}',  # b5 (? 포함 -> CLOSING-QUESTION 위반)
            '{"text": "다음에 또 봬요"}',  # b5 재생성 (물음표 없음)
        ]

        graph = create_simulation_graph(llm=fake_llm, write_report=lambda s: {"status": "ok"})

        initial_state = {
            "persona_a": p_a,
            "persona_b": p_b,
            "nickname_a": "알파",
            "nickname_b": "베타",
            "turns": 3,
            "transcript": [],
        }

        async for _ in graph.astream(initial_state):
            pass

        # history 인덱스:
        # 0: a0, 1: b1, 2: a2, 3: b3
        # 4: a4 최초, 5: a4 재생성
        # 6: b5 최초, 7: b5 재생성

        # a4 최초 및 재생성
        for h in [fake_llm.history[4], fake_llm.history[5]]:
            sys_content = h[0]["content"]
            assert HINT_NO_NEW_QUESTION in sys_content
            assert HINT_CLOSE not in sys_content

        # b5 최초 및 재생성
        for h in [fake_llm.history[6], fake_llm.history[7]]:
            sys_content = h[0]["content"]
            assert HINT_CLOSE in sys_content
            assert HINT_NO_NEW_QUESTION not in sys_content

        # 앞선 턴들에는 힌트가 없어야 함
        for idx in range(4):
            sys_content = fake_llm.history[idx][0]["content"]
            assert HINT_NO_NEW_QUESTION not in sys_content
            assert HINT_CLOSE not in sys_content

    asyncio.run(_run())


def test_last_b_double_question_mark_causes_closing_failed(
    test_personas: tuple[PersonaResponse, PersonaResponse],
) -> None:
    """마지막 b가 ?로 두 번 답하면 closing_failed, transcript 길이는 turns*2-1, report 호출 0 검증."""

    async def _run() -> None:
        p_a, p_b = test_personas
        fake_llm = FakeLLM()
        report_called = False

        async def mock_report(state: SimulationGraphState) -> dict:
            nonlocal report_called
            report_called = True
            return {}

        fake_llm.responses = [
            '{"text": "대사 0"}',  # a0
            '{"text": "대사 1"}',  # b1
            '{"text": "대사 2"}',  # a2
            '{"text": "대사 3"}',  # b3
            '{"text": "대사 4"}',  # a4
            '{"text": "다음에 커피 마실래요?"}',  # b5 최초 (? 위반)
            '{"text": "내일 연락해도 될까요?"}',  # b5 재생성 (? 재위반)
        ]

        graph = create_simulation_graph(llm=fake_llm, write_report=mock_report)

        initial_state = {
            "persona_a": p_a,
            "persona_b": p_b,
            "nickname_a": "알파",
            "nickname_b": "베타",
            "turns": 3,
            "transcript": [],
        }

        final_state = await graph.ainvoke(initial_state)

        assert final_state["error"] == "closing_failed"
        assert len(final_state["transcript"]) == 5  # turns*2 - 1
        assert report_called is False

    asyncio.run(_run())


def test_guardrail_regeneration_and_second_violation(
    test_personas: tuple[PersonaResponse, PersonaResponse],
) -> None:
    """가드레일 재생성 1회 후 두 번째도 위반이면 guardrail이고 그 줄은 저장되지 않으며 report 호출 0 검증."""

    async def _run() -> None:
        p_a, p_b = test_personas
        fake_llm = FakeLLM()
        report_called = False

        async def mock_report(state: SimulationGraphState) -> dict:
            nonlocal report_called
            report_called = True
            return {}

        # 첫 턴 a에서 self_addressed 2회 연속 발생
        fake_llm.responses = [
            '{"text": "알파님 반갑습니다"}',  # 최초 위반
            '{"text": "알파님 또 말하네요"}',  # 재생성 위반
        ]

        graph = create_simulation_graph(llm=fake_llm, write_report=mock_report)

        initial_state = {
            "persona_a": p_a,
            "persona_b": p_b,
            "nickname_a": "알파",
            "nickname_b": "베타",
            "turns": 3,
            "transcript": [],
        }

        final_state = await graph.ainvoke(initial_state)

        assert final_state["error"] == "guardrail"
        assert len(final_state["transcript"]) == 0
        assert report_called is False

    asyncio.run(_run())


def test_empty_response_format_retry_once_no_third_call(
    test_personas: tuple[PersonaResponse, PersonaResponse],
) -> None:
    """빈 응답은 형식 재시도 1회만 하며 세 번째 호출은 없고 실패 처리 검증."""

    async def _run() -> None:
        p_a, p_b = test_personas
        fake_llm = FakeLLM()
        report_called = False

        async def mock_report(state: SimulationGraphState) -> dict:
            nonlocal report_called
            report_called = True
            return {}

        fake_llm.responses = [
            "",  # 최초: 빈 값
            '{"text": ""}',  # 재시도: 빈 대사
        ]

        graph = create_simulation_graph(llm=fake_llm, write_report=mock_report)

        initial_state = {
            "persona_a": p_a,
            "persona_b": p_b,
            "nickname_a": "알파",
            "nickname_b": "베타",
            "turns": 3,
            "transcript": [],
        }

        final_state = await graph.ainvoke(initial_state)

        # 1회 최초 + 1회 형식 재시도 = 총 2회 호출 (3번째 호출 없음)
        assert len(fake_llm.history) == 2
        assert final_state["error"] == "invalid_json"
        assert len(final_state["transcript"]) == 0
        assert report_called is False

    asyncio.run(_run())


def test_failed_node_stops_without_calling_report(test_personas: tuple[PersonaResponse, PersonaResponse]) -> None:
    """실패 노드 뒤에는 report가 호출되지 않고 stop으로 이동 검증."""

    async def _run() -> None:
        p_a, p_b = test_personas
        fake_llm = FakeLLM()
        report_called = False

        async def mock_report(state: SimulationGraphState) -> dict:
            nonlocal report_called
            report_called = True
            return {}

        # 3번째 대사(speak_a, index 2)에서 실패 유도
        fake_llm.responses = [
            '{"text": "대사 0"}',  # speak_a 성공
            '{"text": "대사 1"}',  # speak_b 성공
            "",  # speak_a 형식 실패
            "",  # speak_a 형식 재시도 실패
        ]

        graph = create_simulation_graph(llm=fake_llm, write_report=mock_report)

        initial_state = {
            "persona_a": p_a,
            "persona_b": p_b,
            "nickname_a": "알파",
            "nickname_b": "베타",
            "turns": 3,
            "transcript": [],
        }

        final_state = await graph.ainvoke(initial_state)

        assert final_state["error"] == "invalid_json"
        assert len(final_state["transcript"]) == 2
        assert report_called is False

    asyncio.run(_run())


def test_compiled_graph_has_no_checkpointer() -> None:
    """컴파일된 그래프에 체크포인터가 없는지 검증."""
    graph = create_simulation_graph()
    assert getattr(graph, "checkpointer", None) is None


def test_extract_partner_attributes_includes_date_prefer_and_excludes_speaker_overlap() -> None:
    """화자 속성과 겹치는 값은 빠지고, date_prefer만 있어도 partner_attributes에 들어가는지 검증."""
    from app.features.simulation_migration.graph import _extract_partner_attributes

    p_a = PersonaResponse(
        persona_id="p_a",
        scores={},
        interests=["운동", "독서"],
        routine=["새벽 조깅"],
        date_prefer=["주말 브런치"],
        narrative=Narrative(headline="A", body="A", traits=[]),
    )
    p_b = PersonaResponse(
        persona_id="p_b",
        scores={},
        interests=["독서", "영화"],
        routine=["저녁 산책"],
        date_prefer=["드라이브"],
        narrative=Narrative(headline="B", body="B", traits=[]),
    )

    # 1. A가 화자일 때 (상대 B 속성 추출): "독서"는 겹치므로 제외, B의 date_prefer인 "드라이브"는 포함
    attrs_for_a = _extract_partner_attributes(p_a, p_b)
    assert "독서" not in attrs_for_a
    assert "영화" in attrs_for_a
    assert "저녁 산책" in attrs_for_a
    assert "드라이브" in attrs_for_a
    assert attrs_for_a == ["영화", "저녁 산책", "드라이브"]

    # 2. B가 화자일 때 (상대 A 속성 추출): "독서"는 겹치므로 제외, A의 date_prefer인 "주말 브런치"는 포함
    attrs_for_b = _extract_partner_attributes(p_b, p_a)
    assert "독서" not in attrs_for_b
    assert "운동" in attrs_for_b
    assert "새벽 조깅" in attrs_for_b
    assert "주말 브런치" in attrs_for_b
    assert attrs_for_b == ["운동", "새벽 조깅", "주말 브런치"]

    # 3. date_prefer만 있는 경우도 상대 속성에 포함되는지 검증
    p_only_date_a = PersonaResponse(
        persona_id="p_oda",
        scores={},
        interests=[],
        routine=[],
        date_prefer=["전시회 관람"],
        narrative=Narrative(headline="ODA", body="ODA", traits=[]),
    )
    p_only_date_b = PersonaResponse(
        persona_id="p_odb",
        scores={},
        interests=[],
        routine=[],
        date_prefer=["맛집 탐방"],
        narrative=Narrative(headline="ODB", body="ODB", traits=[]),
    )
    attrs_only_date = _extract_partner_attributes(p_only_date_a, p_only_date_b)
    assert attrs_only_date == ["맛집 탐방"]


def test_tools_populates_speaker_attributes_in_guardrail_context() -> None:
    """tools.py의 speak_as_persona가 화자의 interests, routine, date_prefer를 GuardrailContext.speaker_attributes에 넣는지 검증."""
    import app.features.simulation_migration.tools as tools_mod
    from app.core.guardrail import Grade, ValidationResult
    from app.features.simulation_migration.tools import speak_as_a

    p_a = PersonaResponse(
        persona_id="p_a",
        scores={},
        interests=["클라이밍"],
        routine=["모닝 커피"],
        date_prefer=["전시회"],
        narrative=Narrative(headline="A", body="A", traits=[]),
    )

    captured_contexts = []

    def mock_validate(text, ctx):
        captured_contexts.append(ctx)
        return ValidationResult(
            status="PASS",
            grade=Grade.PASS,
            initial_grade=Grade.PASS,
            regenerated=False,
            violations=[],
            latency_ms=1,
        )

    async def _run() -> None:
        fake_llm = FakeLLM(default_response='{"text": "안녕하세요"}')
        orig_validate = tools_mod.validate
        tools_mod.validate = mock_validate
        try:
            res = await speak_as_a(
                persona=p_a,
                nickname_self="알파",
                nickname_other="베타",
                transcript=[],
                closing_hint=None,
                llm=fake_llm,
                partner_attributes=["보드게임"],
            )
            assert res.text == "안녕하세요"
            assert len(captured_contexts) == 1
            ctx = captured_contexts[0]
            assert ctx.speaker_name == "알파"
            assert ctx.partner_name == "베타"
            assert ctx.speaker_attributes == ["클라이밍", "모닝 커피", "전시회"]
            assert ctx.partner_attributes == ["보드게임"]
        finally:
            tools_mod.validate = orig_validate

    asyncio.run(_run())


def test_warn_grade_does_not_regenerate_and_saves_line_with_validation() -> None:
    """WARN 위반만 있는 경우 대사를 재생성하지 않고 횟수를 쓰지 않으며 validation 결과를 남긴 채 그대로 저장 검증."""
    import app.features.simulation_migration.tools as tools_mod
    from app.core.guardrail import Domain, Grade, ValidationResult, Violation
    from app.features.simulation_migration.tools import speak_as_a

    p_a = PersonaResponse(
        persona_id="p_a",
        scores={},
        interests=["음악"],
        routine=["산책"],
        date_prefer=[],
        narrative=Narrative(headline="A", body="A", traits=[]),
    )

    warn_violation = Violation(
        domain=Domain.STYLE,
        rule_id="RULE-WARN-STYLE",
        description="주의 요망",
        severity=Grade.WARN,
    )
    warn_result = ValidationResult(
        status="WARN",
        grade=Grade.WARN,
        initial_grade=Grade.WARN,
        regenerated=False,
        violations=[warn_violation],
        latency_ms=1,
    )

    validate_call_count = 0

    def mock_validate(text, ctx):
        nonlocal validate_call_count
        validate_call_count += 1
        return warn_result

    async def _run() -> None:
        fake_llm = FakeLLM(default_response='{"text": "약간의 워닝 대사입니다"}')
        orig_validate = tools_mod.validate
        tools_mod.validate = mock_validate
        try:
            res = await speak_as_a(
                persona=p_a,
                nickname_self="알파",
                nickname_other="베타",
                transcript=[],
                closing_hint=None,
                llm=fake_llm,
                partner_attributes=[],
            )
            # 1. 대사가 버려지지 않고 그대로 저장
            assert res.text == "약간의 워닝 대사입니다"
            # 2. 재생성이 발생하지 않았으므로 LLM 호출 1회, validate 1회
            assert res.llm_calls == 1
            assert len(fake_llm.history) == 1
            assert validate_call_count == 1
            # 3. validation 결과에 WARN과 위반 정보가 유지됨
            assert res.validation is not None
            assert res.validation.grade == Grade.WARN
            assert len(res.validation.violations) == 1
            assert res.validation.violations[0].rule_id == "RULE-WARN-STYLE"
            assert res.reason is None
        finally:
            tools_mod.validate = orig_validate

    asyncio.run(_run())


def test_retryable_grade_triggers_regeneration_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """RETRYABLE 위반 시 1회 재생성(2번째 LLM 호출)이 수행되고 통과 시 대사가 저장되는지 검증."""
    monkeypatch.setenv("GUARDRAIL_MODE", "enforce")
    import app.features.simulation_migration.tools as tools_mod
    from app.core.guardrail import Domain, Grade, ValidationResult, Violation
    from app.features.simulation_migration.tools import speak_as_a

    p_a = PersonaResponse(
        persona_id="p_a",
        scores={},
        interests=["음악"],
        routine=["산책"],
        date_prefer=[],
        narrative=Narrative(headline="A", body="A", traits=[]),
    )

    retryable_result = ValidationResult(
        status="REGENERATED",
        grade=Grade.RETRYABLE,
        initial_grade=Grade.RETRYABLE,
        regenerated=False,
        violations=[
            Violation(
                domain=Domain.FACT,
                rule_id="RULE-RETRYABLE-FACT",
                description="재시도 필요",
                severity=Grade.RETRYABLE,
            )
        ],
        latency_ms=1,
    )
    pass_result = ValidationResult(
        status="PASS",
        grade=Grade.PASS,
        initial_grade=Grade.PASS,
        regenerated=False,
        violations=[],
        latency_ms=1,
    )

    validate_call_count = 0

    def mock_validate(text, ctx):
        nonlocal validate_call_count
        validate_call_count += 1
        if validate_call_count == 1:
            return retryable_result
        return pass_result

    async def _run() -> None:
        fake_llm = FakeLLM()
        fake_llm.responses = [
            '{"text": "첫번째 문제대사"}',
            '{"text": "두번째 수정대사"}',
        ]
        orig_validate = tools_mod.validate
        tools_mod.validate = mock_validate
        try:
            res = await speak_as_a(
                persona=p_a,
                nickname_self="알파",
                nickname_other="베타",
                transcript=[],
                closing_hint=None,
                llm=fake_llm,
                partner_attributes=[],
            )
            # 1. 2회차 대사로 최종 성공
            assert res.text == "두번째 수정대사"
            # 2. 재생성으로 인해 LLM 호출 2회, validate 호출 2회
            assert res.llm_calls == 2
            assert validate_call_count == 2
            assert res.validation.grade == Grade.PASS
            assert res.reason is None
        finally:
            tools_mod.validate = orig_validate

    asyncio.run(_run())


def test_simulation_graph_observable_acceptance_warn_and_partner_date_prefer() -> None:
    """Observable acceptance: WARN은 재생성하지 않고 대사로 저장되며, date_prefer만 있는 상대 속성이 partner_attributes에 들어가고 화자와 겹치는 값은 빠지는 것을 그래프 실행으로 검증."""
    import app.features.simulation_migration.tools as tools_mod
    from app.core.guardrail import Domain, Grade, ValidationResult, Violation

    # p_a: interests=["영화", "독서"], date_prefer=["주말 드라이브"]
    p_a = PersonaResponse(
        persona_id="p_a",
        scores={},
        interests=["영화", "독서"],
        routine=[],
        date_prefer=["주말 드라이브"],
        narrative=Narrative(headline="A", body="A", traits=[]),
    )
    # p_b: interests=["독서", "등산"], date_prefer=["전시회"]
    p_b = PersonaResponse(
        persona_id="p_b",
        scores={},
        interests=["독서", "등산"],
        routine=[],
        date_prefer=["전시회"],
        narrative=Narrative(headline="B", body="B", traits=[]),
    )

    captured_contexts = []
    orig_validate = tools_mod.validate

    def mock_validate(text, ctx):
        captured_contexts.append(ctx)
        # 첫 턴에 WARN 발생
        if len(captured_contexts) == 1:
            return ValidationResult(
                status="WARN",
                grade=Grade.WARN,
                initial_grade=Grade.WARN,
                regenerated=False,
                violations=[
                    Violation(
                        domain=Domain.STYLE,
                        rule_id="RULE-WARN",
                        description="경고",
                        severity=Grade.WARN,
                    )
                ],
                latency_ms=1,
            )
        return ValidationResult(
            status="PASS",
            grade=Grade.PASS,
            initial_grade=Grade.PASS,
            regenerated=False,
            violations=[],
            latency_ms=1,
        )

    async def _run() -> None:
        fake_llm = FakeLLM()
        fake_llm.responses = [
            '{"text": "A의 첫 대사 (WARN)"}',
            '{"text": "B의 첫 대사 (PASS)"}',
        ]
        tools_mod.validate = mock_validate
        try:
            graph = create_simulation_graph(llm=fake_llm, write_report=lambda s: {"status": "done"})
            initial_state = {
                "persona_a": p_a,
                "persona_b": p_b,
                "nickname_a": "알파",
                "nickname_b": "베타",
                "turns": 3,
                "transcript": [],
            }
            final_state = await graph.ainvoke(initial_state)

            # 1. WARN 대사가 재생성 없이 그대로 transcript에 포함됨 (총 대사 6개)
            assert len(final_state["transcript"]) == 6
            assert final_state["transcript"][0] == ("a", "A의 첫 대사 (WARN)")
            assert final_state["transcript"][1] == ("b", "B의 첫 대사 (PASS)")
            assert len(fake_llm.history) == 6  # WARN으로 인한 재생성 없음 (정확히 6회 호출)!

            # 2. 첫 턴(A 화자) 가드레일 컨텍스트 검증:
            # - 화자 속성: ["영화", "독서", "주말 드라이브"]
            # - 상대 속성: B의 ["독서", "등산", "전시회"] 중 "독서"는 화자와 겹치므로 제외 -> ["등산", "전시회"]
            # - B의 date_prefer인 "전시회"가 포함됨!
            ctx_a = captured_contexts[0]
            assert ctx_a.speaker_attributes == ["영화", "독서", "주말 드라이브"]
            assert ctx_a.partner_attributes == ["등산", "전시회"]
            assert "독서" not in ctx_a.partner_attributes
            assert "전시회" in ctx_a.partner_attributes

            # 3. 둘째 턴(B 화자) 가드레일 컨텍스트 검증:
            # - 화자 속성: ["독서", "등산", "전시회"]
            # - 상대 속성: A의 ["영화", "독서", "주말 드라이브"] 중 "독서"는 겹치므로 제외 -> ["영화", "주말 드라이브"]
            # - A의 date_prefer인 "주말 드라이브"가 포함됨!
            ctx_b = captured_contexts[1]
            assert ctx_b.speaker_attributes == ["독서", "등산", "전시회"]
            assert ctx_b.partner_attributes == ["영화", "주말 드라이브"]
            assert "독서" not in ctx_b.partner_attributes
            assert "주말 드라이브" in ctx_b.partner_attributes
        finally:
            tools_mod.validate = orig_validate

    asyncio.run(_run())


def test_shadow_mode_guardrail_violation_not_regenerated_m2(monkeypatch: pytest.MonkeyPatch) -> None:
    """[M2] shadow 모드에서 validate가 CheckResult(BLOCK)를 반환해도 재생성 없이 1회차 대사가 성공 반환되고 ValidationResult(status='SHADOW_FAIL')로 변환됨을 검증."""
    monkeypatch.setenv("GUARDRAIL_MODE", "shadow")
    import app.features.simulation_migration.tools as tools_mod
    from app.core.guardrail import CheckResult, Domain, Grade, ValidationResult, Violation
    from app.features.simulation_migration.tools import speak_as_a

    p_a = PersonaResponse(
        persona_id="p_a",
        scores={},
        interests=["음악"],
        routine=[],
        date_prefer=[],
        narrative=Narrative(headline="A", body="A", traits=[]),
    )

    # validate()의 실제 반환 타입인 CheckResult 생성
    block_check_result = CheckResult(
        grade=Grade.BLOCK,
        violations=[
            Violation(
                domain=Domain.FACT,
                rule_id="RULE-FACT",
                description="사실 불일치",
                severity=Grade.BLOCK,
            )
        ],
        latency_ms=1,
    )

    validate_called = 0

    def mock_validate(text, ctx):
        nonlocal validate_called
        validate_called += 1
        return block_check_result

    async def _run() -> None:
        fake_llm = FakeLLM()
        fake_llm.responses = [
            '{"text": "첫번째 대사"}',
            '{"text": "두번째 대사"}',
        ]
        orig_validate = tools_mod.validate
        tools_mod.validate = mock_validate
        try:
            res = await speak_as_a(
                persona=p_a,
                nickname_self="알파",
                nickname_other="베타",
                transcript=[],
                closing_hint=None,
                llm=fake_llm,
                partner_attributes=[],
            )
            # shadow 모드이므로 1회 호출 후 재생성 없이 첫 번째 대사 반환
            assert res.text == "첫번째 대사"
            assert res.llm_calls == 1
            assert validate_called == 1
            assert res.validation is not None
            # CheckResult가 ValidationResult로 변환되었고 status가 SHADOW_FAIL인지 검증
            assert isinstance(res.validation, ValidationResult)
            assert res.validation.status == "SHADOW_FAIL"
            assert res.validation.grade == Grade.BLOCK
            assert res.validation.initial_grade == Grade.BLOCK
            assert res.validation.regenerated is False
            assert res.reason is None
        finally:
            tools_mod.validate = orig_validate

    asyncio.run(_run())


def test_fullwidth_question_mark_closing_failed_m2() -> None:
    """[m2] 마지막 b 대사(closing_hint='close')에서 전각 물음표(？) 포함 시 재생성 후에도 계속되면 closing_failed로 실패 검증."""
    from app.features.simulation_migration.tools import speak_as_b

    p_b = PersonaResponse(
        persona_id="p_b",
        scores={},
        interests=["영화"],
        routine=[],
        date_prefer=[],
        narrative=Narrative(headline="B", body="B", traits=[]),
    )

    async def _run() -> None:
        fake_llm = FakeLLM()
        # 1회차, 2회차 모두 전각 물음표 '？' 포함
        fake_llm.responses = [
            '{"text": "다음에 또 봐요？"}',
            '{"text": "정말 즐거웠어요？"}',
        ]
        res = await speak_as_b(
            persona=p_b,
            nickname_self="베타",
            nickname_other="알파",
            transcript=[("a", "오늘 즐거웠어요")],
            closing_hint="close",
            llm=fake_llm,
            partner_attributes=[],
        )
        # 전각 물음표로 인해 2회차까지 시도 후 closing_failed 반환
        assert res.text is None
        assert res.llm_calls == 2
        assert res.reason == "closing_failed"

    asyncio.run(_run())


def test_utterance_validation_saved_in_db_and_guardrail_recorded_m1_m2(monkeypatch: pytest.MonkeyPatch) -> None:
    """[m1, M2] 발화 완료 시 DB 행의 validation 컬럼에 결과가 저장되고 record_guardrail이 호출되는지 검증."""
    from conftest import seed_persona, with_db

    from app.core.guardrail import Grade, ValidationResult
    from app.features.simulation_migration.manager import RunManager
    from app.features.simulation_migration.repository import MigrationRepository

    guardrail_records: list[dict] = []

    async def mock_record_guardrail(*args, **kwargs):
        guardrail_records.append(kwargs)

    monkeypatch.setattr("app.core.guardrail_trace.record_guardrail", mock_record_guardrail)

    dummy_val = ValidationResult(
        status="PASS",
        grade=Grade.PASS,
        initial_grade=Grade.PASS,
        regenerated=False,
        violations=[],
        latency_ms=5,
    )

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")

        p_a = PersonaResponse(
            persona_id="pa",
            scores={},
            interests=[],
            routine=[],
            date_prefer=[],
            narrative=Narrative(headline="A", body="A", traits=[]),
        )
        p_b = PersonaResponse(
            persona_id="pb",
            scores={},
            interests=[],
            routine=[],
            date_prefer=[],
            narrative=Narrative(headline="B", body="B", traits=[]),
        )

        class CustomGraph:
            async def astream(self, initial_state):
                # 1턴 대사 방출 (validation 포함)
                yield {
                    "speak_a": {
                        "transcript": [("a", "테스트 발화")],
                        "validation": dummy_val,
                    }
                }
                yield {"report": {"report": {"status": "done"}}}

        manager = RunManager(factory, max_runs=2, build_graph=lambda *a, **kw: CustomGraph())

        task = await manager.start_run(
            persona_a=p_a,
            persona_b=p_b,
            user_id_a="ua",
            user_id_b="ub",
            nickname_a="A",
            nickname_b="B",
            turns=1,
            write_report=lambda s: {"status": "done"},
        )
        run_id = task.run_id
        await task

        # 1. DB의 utterance.validation 검증 [m1]
        async with factory() as db:
            repo = MigrationRepository(db)
            utts = await repo.list_utterances(run_id)
            assert len(utts) == 1
            assert utts[0].validation is not None
            assert utts[0].validation["grade"] == "PASS"

        # 2. record_guardrail 호출 검증 [M2]
        assert len(guardrail_records) >= 1
        rec = guardrail_records[0]
        assert rec["feature"] == "simulation"
        assert rec["operation"] == "migration_line"
        assert rec["session_id"] == run_id
        assert rec["initial_text"] == "테스트 발화"
        assert rec["result"].grade == Grade.PASS

    asyncio.run(with_db(scenario))


def test_shadow_block_checkresult_saved_and_record_guardrail_status_shadow_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[M2] shadow 모드에서 validate가 CheckResult(BLOCK)를 반환할 때 재생성 없이 대사가 저장되고 record_guardrail에 status=SHADOW_FAIL인 ValidationResult가 전달되는지 검증."""
    monkeypatch.setenv("GUARDRAIL_MODE", "shadow")
    from conftest import seed_persona, with_db

    import app.features.simulation_migration.tools as tools_mod
    from app.core.guardrail import CheckResult, Domain, Grade, ValidationResult, Violation
    from app.features.simulation_migration.graph import create_simulation_graph
    from app.features.simulation_migration.manager import RunManager
    from app.features.simulation_migration.repository import MigrationRepository

    guardrail_records: list[dict] = []

    async def mock_record_guardrail(*args, **kwargs):
        guardrail_records.append(kwargs)

    monkeypatch.setattr("app.core.guardrail_trace.record_guardrail", mock_record_guardrail)

    orig_validate = tools_mod.validate
    tools_mod.validate = lambda text, ctx: CheckResult(
        grade=Grade.BLOCK,
        violations=[
            Violation(
                domain=Domain.FACT,
                rule_id="RULE-FACT",
                description="사실 불일치",
                severity=Grade.BLOCK,
            )
        ],
        latency_ms=10,
    )

    async def scenario(factory):
        async with factory() as db:
            await seed_persona(db, persona_id="pa", user_id="ua", nickname="A")
            await seed_persona(db, persona_id="pb", user_id="ub", nickname="B")

        p_a = PersonaResponse(
            persona_id="pa",
            scores={},
            interests=["영화"],
            routine=[],
            date_prefer=[],
            narrative=Narrative(headline="A", body="A", traits=[]),
        )
        p_b = PersonaResponse(
            persona_id="pb",
            scores={},
            interests=["독서"],
            routine=[],
            date_prefer=[],
            narrative=Narrative(headline="B", body="B", traits=[]),
        )

        fake_llm = FakeLLM(default_response='{"text": "shadow 블록 대사"}')

        manager = RunManager(
            factory,
            max_runs=2,
            build_graph=lambda *a, **kw: create_simulation_graph(
                llm=fake_llm,
                write_report=lambda s: {"status": "done"},
            ),
        )

        try:
            task = await manager.start_run(
                persona_a=p_a,
                persona_b=p_b,
                user_id_a="ua",
                user_id_b="ub",
                nickname_a="A",
                nickname_b="B",
                turns=3,
                write_report=lambda s: {"status": "done"},
            )
            run_id = task.run_id
            await task

            # 1. DB의 utterance 저장 확인 (재생성 없이 그대로 저장)
            async with factory() as db:
                repo = MigrationRepository(db)
                utts = await repo.list_utterances(run_id)
                assert len(utts) == 6
                assert utts[0].text == "shadow 블록 대사"
                assert utts[0].validation is not None
                assert utts[0].validation["status"] == "SHADOW_FAIL"
                assert utts[0].validation["grade"] == "BLOCK"

            # 2. record_guardrail에 전달된 result 확인
            assert len(guardrail_records) >= 1
            rec = guardrail_records[0]
            assert rec["feature"] == "simulation"
            assert rec["operation"] == "migration_line"
            assert rec["session_id"] == run_id
            assert rec["mode"] == "shadow"
            # ValidationResult 객체여야 하며 status가 SHADOW_FAIL이어야 함
            assert isinstance(rec["result"], ValidationResult)
            assert rec["result"].status == "SHADOW_FAIL"
            assert rec["result"].grade == Grade.BLOCK
            assert rec["result"].initial_grade == Grade.BLOCK
            assert rec["result"].regenerated is False
        finally:
            tools_mod.validate = orig_validate

    asyncio.run(with_db(scenario))


def test_regenerated_checkresult_to_validationresult(monkeypatch: pytest.MonkeyPatch) -> None:
    """내용 재생성이 발생하고 통과한 경우 status=REGENERATED, regenerated=True, initial_grade=첫 검사 grade로 변환됨을 검증."""
    monkeypatch.setenv("GUARDRAIL_MODE", "enforce")
    import app.features.simulation_migration.tools as tools_mod
    from app.core.guardrail import CheckResult, Domain, Grade, ValidationResult, Violation
    from app.features.simulation_migration.tools import speak_as_a

    p_a = PersonaResponse(
        persona_id="p_a",
        scores={},
        interests=["음악"],
        routine=[],
        date_prefer=[],
        narrative=Narrative(headline="A", body="A", traits=[]),
    )

    retryable_check = CheckResult(
        grade=Grade.RETRYABLE,
        violations=[
            Violation(
                domain=Domain.FACT,
                rule_id="RULE-RETRYABLE",
                description="재시도 필요",
                severity=Grade.RETRYABLE,
            )
        ],
        latency_ms=1,
    )
    pass_check = CheckResult(
        grade=Grade.PASS,
        violations=[],
        latency_ms=2,
    )

    validate_call_count = 0

    def mock_validate(text, ctx):
        nonlocal validate_call_count
        validate_call_count += 1
        if validate_call_count == 1:
            return retryable_check
        return pass_check

    async def _run() -> None:
        fake_llm = FakeLLM()
        fake_llm.responses = [
            '{"text": "첫번째 문제대사"}',
            '{"text": "두번째 통과대사"}',
        ]
        orig_validate = tools_mod.validate
        tools_mod.validate = mock_validate
        try:
            res = await speak_as_a(
                persona=p_a,
                nickname_self="알파",
                nickname_other="베타",
                transcript=[],
                closing_hint=None,
                llm=fake_llm,
                partner_attributes=[],
            )
            # 재생성으로 인해 통과대사 반환
            assert res.text == "두번째 통과대사"
            assert res.llm_calls == 2
            assert validate_call_count == 2
            assert isinstance(res.validation, ValidationResult)
            # REGENERATED 검증
            assert res.validation.status == "REGENERATED"
            assert res.validation.regenerated is True
            assert res.validation.initial_grade == Grade.RETRYABLE
            assert res.validation.grade == Grade.PASS
            assert res.reason is None
        finally:
            tools_mod.validate = orig_validate

    asyncio.run(_run())
