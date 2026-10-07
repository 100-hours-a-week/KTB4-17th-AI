from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal

import pytest

from app.features.persona.profile import describe
from app.features.persona.schemas import Narrative, PersonaResponse
from app.features.simulation.schemas import (
    AreaReport,
    DateSuggestion,
    MatchingReport,
    Overall,
    ReportConfidence,
)
from app.features.simulation_migration.order import (
    arc_phase_for,
    closing_hint_for,
    contains_farewell,
    next_node,
    repeats_previous,
    self_addressed,
    strip_name_prefix,
)
from app.features.simulation_migration.prompts import (
    HINT_CLOSE,
    HINT_LATE,
    HINT_MIDDLE,
    HINT_NO_NEW_QUESTION,
    HINT_OPEN,
    OPENER,
    parse_speaker_json,
    reply_anchor,
    speaker_messages,
)
from app.features.simulation_migration.schemas import (
    MigrationAccepted,
    MigrationDoneEvent,
    MigrationErrorEvent,
    MigrationListItem,
    MigrationReportEvent,
    MigrationReportView,
    MigrationRunEvent,
    MigrationRunView,
    MigrationUtteranceEvent,
    MigrationUtteranceView,
)
from app.features.simulation_migration.state import MigrationState
from app.features.simulation_migration.tools import speak_as_b


@pytest.fixture
def personas() -> tuple[PersonaResponse, PersonaResponse]:
    """describe 결과가 서로 겹치지 않는 문장을 가진 두 페르소나 픽스처."""
    p_a = PersonaResponse(
        persona_id="persona_alpha",
        scores={},
        interests=["산행", "트레킹"],
        routine=["새벽 조깅"],
        narrative=Narrative(
            headline="산이 좋은 등산가",
            body="알파인물만의산행",
            traits=["등산애호", "자연선호"],
        ),
    )
    p_b = PersonaResponse(
        persona_id="persona_beta",
        scores={},
        interests=["독서", "서점탐방"],
        routine=["심야 책읽기"],
        narrative=Narrative(
            headline="책이 좋은 문학인",
            body="베타인물만의서점",
            traits=["독서애호", "문학선호"],
        ),
    )
    return p_a, p_b


def test_next_node_turns_3_call_sequence() -> None:
    """turns=3일 때 speak_a, speak_b, speak_a, speak_b, speak_a, speak_b, report 순서 검증."""
    expected = ["speak_a", "speak_b", "speak_a", "speak_b", "speak_a", "speak_b", "report"]
    actual = [next_node(utterance_count=i, turns=3, error=None) for i in range(7)]
    assert actual == expected


def test_next_node_turns_15_call_sequence() -> None:
    """turns=15일 때 30번째 전까지는 report가 아니고, 30번째에서 정확히 report 반환 검증."""
    for i in range(30):
        res = next_node(utterance_count=i, turns=15, error=None)
        assert res != "report"
        expected = "speak_a" if i % 2 == 0 else "speak_b"
        assert res == expected

    assert next_node(utterance_count=30, turns=15, error=None) == "report"


def test_next_node_error_and_out_of_bounds() -> None:
    """error가 있으면 0 대사여도 stop, utterance_count가 turns*2 초과면 stop 반환 검증."""
    assert next_node(utterance_count=0, turns=3, error="some_error") == "stop"
    assert next_node(utterance_count=2, turns=3, error="timeout") == "stop"

    # turns * 2 초과 시 stop
    assert next_node(utterance_count=7, turns=3, error=None) == "stop"
    assert next_node(utterance_count=31, turns=15, error=None) == "stop"

    # turns 범위 검증
    with pytest.raises(ValueError, match="turns는 3 이상 15 이하여야 합니다"):
        next_node(utterance_count=0, turns=2)

    with pytest.raises(ValueError, match="turns는 3 이상 15 이하여야 합니다"):
        next_node(utterance_count=0, turns=16)


def test_closing_hint_turns_3_and_15() -> None:
    """마지막 a(turns*2-2)는 no_new_question, 마지막 b(turns*2-1)는 close, 그 외 none 검증."""
    # turns = 3 (총 6대사: 0, 1, 2, 3, 4, 5)
    for i in range(4):
        assert closing_hint_for(index=i, turns=3) == "none"
    assert closing_hint_for(index=4, turns=3) == "no_new_question"
    assert closing_hint_for(index=5, turns=3) == "close"

    # turns = 15 (총 30대사: 0 ~ 29)
    for i in range(28):
        assert closing_hint_for(index=i, turns=15) == "none"
    assert closing_hint_for(index=28, turns=15) == "no_new_question"
    assert closing_hint_for(index=29, turns=15) == "close"


def test_speaker_messages_profile_isolation(personas: tuple[PersonaResponse, PersonaResponse]) -> None:
    """a 메시지에는 describe(a)만 있고 상대의 비중첩 문장이 없으며, b도 대칭적으로 검증."""
    p_a, p_b = personas
    desc_a = describe("알파", p_a)
    desc_b = describe("베타", p_b)

    assert "알파인물만의산행" in desc_a
    assert "베타인물만의서점" not in desc_a
    assert "베타인물만의서점" in desc_b
    assert "알파인물만의산행" not in desc_b

    # a 화자 메시지 생성
    msgs_a = speaker_messages(
        speaker="a",
        persona=p_a,
        nickname_self="알파",
        nickname_other="베타",
        transcript=[("a", "첫인사")],
        closing_hint="none",
    )
    all_content_a = " ".join(m["content"] for m in msgs_a)
    assert "알파인물만의산행" in all_content_a
    assert "베타인물만의서점" not in all_content_a
    assert '상대의 닉네임: "베타"' in msgs_a[0]["content"]

    # b 화자 메시지 생성
    msgs_b = speaker_messages(
        speaker="b",
        persona=p_b,
        nickname_self="베타",
        nickname_other="알파",
        transcript=[("a", "첫인사")],
        closing_hint="none",
    )
    all_content_b = " ".join(m["content"] for m in msgs_b)
    assert "베타인물만의서점" in all_content_b
    assert "알파인물만의산행" not in all_content_b
    assert '상대의 닉네임: "알파"' in msgs_b[0]["content"]


def test_speaker_messages_regeneration_and_hints(personas: tuple[PersonaResponse, PersonaResponse]) -> None:
    """재생성 시 rule_id 문장만 추가되고, 마지막 a와 b의 closing hint가 올바르게 유지되는지 검증."""
    p_a, p_b = personas

    # a 마지막 발화 재생성
    msgs_a_regen = speaker_messages(
        speaker="a",
        persona=p_a,
        nickname_self="알파",
        nickname_other="베타",
        transcript=[("a", "대사1"), ("b", "대사2")],
        closing_hint="no_new_question",
        regen_rule_id="RULE-PERSPECTIVE-SWAP",
    )
    assert msgs_a_regen[-1]["role"] == "user"
    assert msgs_a_regen[-1]["content"] == "규칙 RULE-PERSPECTIVE-SWAP 에 걸렸다. 네 말만 다시 써라."
    all_content_a = " ".join(m["content"] for m in msgs_a_regen)
    assert "베타인물만의서점" not in all_content_a
    # system에 HINT_NO_NEW_QUESTION 있고 HINT_CLOSE 없음
    assert HINT_NO_NEW_QUESTION in msgs_a_regen[0]["content"]
    assert HINT_CLOSE not in msgs_a_regen[0]["content"]

    # b 마지막 발화 재생성
    msgs_b_regen = speaker_messages(
        speaker="b",
        persona=p_b,
        nickname_self="베타",
        nickname_other="알파",
        transcript=[("a", "대사1"), ("b", "대사2"), ("a", "대사3")],
        closing_hint="close",
        regen_rule_id="RULE-PERSPECTIVE-SWAP",
    )
    assert msgs_b_regen[-1]["role"] == "user"
    assert msgs_b_regen[-1]["content"] == "규칙 RULE-PERSPECTIVE-SWAP 에 걸렸다. 네 말만 다시 써라."
    all_content_b = " ".join(m["content"] for m in msgs_b_regen)
    assert "알파인물만의산행" not in all_content_b
    # system에 HINT_CLOSE 있고 HINT_NO_NEW_QUESTION 없음
    assert HINT_CLOSE in msgs_b_regen[0]["content"]
    assert HINT_NO_NEW_QUESTION not in msgs_b_regen[0]["content"]


def test_speaker_messages_initial_opener_and_empty_transcript(
    personas: tuple[PersonaResponse, PersonaResponse],
) -> None:
    """빈 transcript에서 a는 OPENER가 있고 이전 대사가 없으며, b는 ValueError 발생 검증."""
    p_a, p_b = personas

    msgs_a = speaker_messages(
        speaker="a",
        persona=p_a,
        nickname_self="알파",
        nickname_other="베타",
        transcript=[],
        closing_hint="none",
        index=0,
        turns=10,
    )
    assert len(msgs_a) == 1
    assert msgs_a[0]["role"] == "system"
    assert OPENER in msgs_a[0]["content"]
    assert '상대의 닉네임: "베타"' in msgs_a[0]["content"]
    assert HINT_OPEN not in msgs_a[0]["content"]

    with pytest.raises(ValueError, match="speaker b의 대화 시작 시 transcript는 비어 있을 수 없습니다"):
        speaker_messages(
            speaker="b",
            persona=p_b,
            nickname_self="베타",
            nickname_other="알파",
            transcript=[],
            closing_hint="none",
        )


def test_speaker_messages_role_alternation_without_speaker_tags(
    personas: tuple[PersonaResponse, PersonaResponse],
) -> None:
    """transcript (a, "안녕"), (b, "네")를 a와 b에게 줄 때 역할 교대와 이름표 없음을 검증."""
    p_a, p_b = personas
    transcript: list[tuple[Literal["a", "b"], str]] = [("a", "안녕"), ("b", "네")]

    # a 화자 시점: a는 assistant, b는 user
    msgs_a = speaker_messages(
        speaker="a",
        persona=p_a,
        nickname_self="알파",
        nickname_other="베타",
        transcript=transcript,
        closing_hint="none",
    )
    assert msgs_a[1]["role"] == "assistant"
    assert msgs_a[1]["content"] == "안녕"
    assert msgs_a[2]["role"] == "user"
    assert msgs_a[2]["content"] == "네"

    # b 화자 시점: a는 user, b는 assistant
    msgs_b = speaker_messages(
        speaker="b",
        persona=p_b,
        nickname_self="베타",
        nickname_other="알파",
        transcript=transcript,
        closing_hint="none",
    )
    assert msgs_b[1]["role"] == "user"
    assert msgs_b[1]["content"] == "안녕"
    assert msgs_b[2]["role"] == "assistant"
    assert msgs_b[2]["content"] == "네"

    # 이름표가 content에 없는지 검증
    for m in msgs_a[1:] + msgs_b[1:]:
        assert not m["content"].startswith("알파:")
        assert not m["content"].startswith("베타:")
        assert not m["content"].startswith("a:")
        assert not m["content"].startswith("b:")


def test_strip_name_prefix() -> None:
    """strip_name_prefix 이름표 제거 및 두 번째 콜론 보존 동작 검증."""
    assert strip_name_prefix("민지: 안녕", "민지") == "안녕"
    assert strip_name_prefix("민지: 안녕: 두번째", "민지") == "안녕: 두번째"
    assert strip_name_prefix("민지： 안녕", "민지") == "안녕"  # 전각 콜론
    assert strip_name_prefix("  민지:  안녕  ", "민지") == "안녕"
    assert strip_name_prefix("철수: 안녕", "민지") == "철수: 안녕"
    assert strip_name_prefix("안녕", "민지") == "안녕"


def test_self_addressed() -> None:
    """self_addressed 닉네임+님 검증 (2글자 이상만 True, 1글자 False)."""
    assert self_addressed("민지님 안녕", "민지") is True
    assert self_addressed("민님 안녕", "민") is False
    assert self_addressed("민지 안녕", "민지") is False
    assert self_addressed("안녕하세요 민지님 반갑습니다", "민지") is True


def test_parse_speaker_json() -> None:
    """parse_speaker_json 파싱 및 300자 초과/빈값/다른 키에 대한 ValueError 검증."""
    assert parse_speaker_json('{"text":"안녕"}') == "안녕"
    assert parse_speaker_json('{"text": "안녕하세요 ㅎㅎ"}') == "안녕하세요 ㅎㅎ"

    # 다른 키 포함
    with pytest.raises(ValueError, match="'text' 키 하나만 존재해야 합니다"):
        parse_speaker_json('{"text":"안녕", "other":"키"}')

    # 'text' 키 누락
    with pytest.raises(ValueError, match="'text' 키 하나만 존재해야 합니다"):
        parse_speaker_json('{"message":"안녕"}')

    # 빈 값 및 공백만
    with pytest.raises(ValueError, match="대사가 비어 있거나 공백만 포함되어 있습니다"):
        parse_speaker_json('{"text":""}')

    with pytest.raises(ValueError, match="대사가 비어 있거나 공백만 포함되어 있습니다"):
        parse_speaker_json('{"text":"   "}')

    # 301자
    with pytest.raises(ValueError, match="대사는 300자를 초과할 수 없습니다"):
        parse_speaker_json('{"text":"' + ("가" * 301) + '"}')

    # 300자 정상 통과
    exact_300 = "가" * 300
    assert parse_speaker_json('{"text":"' + exact_300 + '"}') == exact_300


def test_migration_state_no_unwanted_fields(personas: tuple[PersonaResponse, PersonaResponse]) -> None:
    """MigrationState 모델 및 인스턴스에 db, session, queue 필드가 없는지 검증."""
    p_a, p_b = personas

    # 클래스 필드 검사
    assert "db" not in MigrationState.model_fields
    assert "session" not in MigrationState.model_fields
    assert "queue" not in MigrationState.model_fields

    assert not hasattr(MigrationState, "db")
    assert not hasattr(MigrationState, "session")
    assert not hasattr(MigrationState, "queue")

    # 인스턴스 검사
    state = MigrationState(
        run_id="run_123",
        attempt=1,
        persona_a=p_a,
        persona_b=p_b,
        nickname_a="알파",
        nickname_b="베타",
        turns=3,
    )
    assert not hasattr(state, "db")
    assert not hasattr(state, "session")
    assert not hasattr(state, "queue")
    assert state.transcript == []
    assert state.error is None


def test_schemas_sse_and_views() -> None:
    """schemas 응답 및 SSE 이벤트 스키마 모델 검증 (delta 필드 부재 확인)."""
    # MigrationAccepted
    acc = MigrationAccepted(simulation_id="sim_1")
    assert acc.simulation_id == "sim_1"

    # MigrationUtteranceView
    uv = MigrationUtteranceView(index=0, speaker="a", nickname="알파", text="안녕")
    assert uv.index == 0

    # MigrationRunView
    rv = MigrationRunView(
        simulation_id="sim_1",
        status="running",
        turns=3,
        attempt=1,
        utterances=[uv],
    )
    assert rv.status == "running"

    # MigrationReportView
    dummy_report = MatchingReport(
        persona_a_id="pa",
        persona_b_id="pb",
        overall=Overall(score=80, grade="GOOD", grade_label="좋음", headline="헤드라인", summary="요약"),
        areas=[
            AreaReport(
                area="intimacy",
                label="거리감",
                score=80,
                grade="GOOD",
                grade_label="좋음",
                comment="설명",
                dimensions=[],
            )
        ],
        date_suggestion=DateSuggestion(suggested=["카페"], avoid=["등산"], comment="추천"),
        confidence=ReportConfidence(accuracy=85, low_dimensions=[], note=""),
    )
    rep_view = MigrationReportView(simulation_id="sim_1", report=dummy_report, narrative_source="llm")
    assert rep_view.narrative_source == "llm"

    # MigrationListItem
    item = MigrationListItem(
        simulation_id="sim_1",
        status="done",
        turns=3,
        created_at=datetime.now(UTC),
    )
    assert item.status == "done"

    # SSE Event 페이로드 5종 및 delta 필드 부재 확인
    e_run = MigrationRunEvent(simulation_id="sim_1", turns=3, status="running")
    assert e_run.event == "run"
    assert "delta" not in MigrationRunEvent.model_fields
    assert not hasattr(e_run, "delta")

    e_utt = MigrationUtteranceEvent(id=0, index=0, speaker="a", nickname="알파", text="안녕")
    assert e_utt.event == "utterance"
    assert "delta" not in MigrationUtteranceEvent.model_fields
    assert not hasattr(e_utt, "delta")

    e_rep = MigrationReportEvent(report=dummy_report)
    assert e_rep.event == "report"
    assert "delta" not in MigrationReportEvent.model_fields
    assert not hasattr(e_rep, "delta")

    e_done = MigrationDoneEvent(simulation_id="sim_1")
    assert e_done.event == "done"
    assert "delta" not in MigrationDoneEvent.model_fields
    assert not hasattr(e_done, "delta")

    e_err = MigrationErrorEvent(reason="timeout", message="호출 시간 초과")
    assert e_err.event == "error"
    assert "delta" not in MigrationErrorEvent.model_fields
    assert not hasattr(e_err, "delta")


def test_speaker_messages_system_prompt_output_format_c1(
    personas: tuple[PersonaResponse, PersonaResponse],
) -> None:
    """[C1] speaker_messages의 system 메시지에 JSON 출력 형식 문장이 항상 포함되는지 검증."""
    from app.features.simulation_migration.prompts import OUTPUT_FORMAT

    p_a, p_b = personas

    for spk, pers, nick_s, nick_o in [
        ("a", p_a, "알파", "베타"),
        ("b", p_b, "베타", "알파"),
    ]:
        for hint in ["none", "no_new_question", "close"]:
            transcripts = [[("a", "안녕하세요")]] if spk == "b" else [[], [("a", "안녕하세요")]]
            for trans in transcripts:
                msgs = speaker_messages(
                    speaker=spk,
                    persona=pers,
                    nickname_self=nick_s,
                    nickname_other=nick_o,
                    transcript=trans,
                    closing_hint=hint,
                )
                system_msg = next((m["content"] for m in msgs if m["role"] == "system"), None)
                assert system_msg is not None
                assert OUTPUT_FORMAT in system_msg
                assert '{"text": "대사"}' in system_msg


def test_self_addressed_boundary_m3() -> None:
    """[m3] self_addressed가 닉네임 앞 경계(?<![가-힣A-Za-z0-9])를 지키는지 검증."""
    # 1. 올바른 자칭 (True)
    assert self_addressed("민수님 안녕하세요", "민수") is True
    assert self_addressed("안녕하세요 민수님!", "민수") is True
    assert self_addressed("오늘 [민수님] 기분은 어때요?", "민수") is True
    assert self_addressed("민수님, 반갑습니다.", "민수") is True

    # 2. 부분 닉네임 오탐 방지 (False) - 앞에 한글, 영문, 숫자가 붙은 경우
    assert self_addressed("강민수님 안녕하세요", "민수") is False
    assert self_addressed("User민수님 안녕하세요", "민수") is False
    assert self_addressed("12민수님 안녕하세요", "민수") is False

    # 3. '님'이 붙지 않은 경우 (False)
    assert self_addressed("민수 안녕", "민수") is False
    assert self_addressed("민수가 왔다", "민수") is False

    # 4. 닉네임 길이 2 미만 (False)
    assert self_addressed("민님 안녕하세요", "민") is False


def test_arc_phase_keeps_farewell_until_the_last_pair() -> None:
    """15왕복에서 작별 금지는 마지막 두 줄 직전까지 유지된다."""
    assert [arc_phase_for(i, 15) for i in range(10)] == ["open"] * 10
    assert [arc_phase_for(i, 15) for i in range(10, 20)] == ["middle"] * 10
    assert [arc_phase_for(i, 15) for i in range(20, 28)] == ["late"] * 8
    assert arc_phase_for(28, 15) == "close_a"
    assert arc_phase_for(29, 15) == "close_b"
    assert arc_phase_for(0, 3) == "open"
    assert arc_phase_for(2, 3) == "middle"
    assert arc_phase_for(4, 3) == "close_a"


def test_farewell_and_repeat_detectors() -> None:
    assert contains_farewell("네 들어가세요 ㅎㅎ 나중에 봐요") is True
    assert contains_farewell("독립서점 분위기 좋았겠어요") is False
    same = "네 잘 들어가세요 ㅎㅎ 다음에 기분 좋게 다시 연락해요."
    near = "네 조심히 들어가세요 ㅎㅎ 다음에 기분 좋게 다시 연락해요."
    assert repeats_previous(near, same) is True
    assert repeats_previous("서점 어디였는지 궁금해요", same) is False


def test_speaker_answers_only_the_last_partner_line(
    personas: tuple[PersonaResponse, PersonaResponse],
) -> None:
    """상대 마지막 문장을 다시 붙인다. 닉네임은 호칭으로만 있고 상대 프로필은 없다."""
    p_b = personas[1]
    last = "그럼 제가 너무 앞서갔나요"
    msgs = speaker_messages(
        speaker="b",
        persona=p_b,
        nickname_self="준호",
        nickname_other="민지",
        transcript=[("b", "무리해서 계획 짜지 않아도 괜찮아요"), ("a", last)],
        closing_hint="none",
        index=7,
        turns=15,
    )
    assert HINT_OPEN in msgs[0]["content"]
    assert '상대의 닉네임: "민지"' in msgs[0]["content"]
    assert "베타인물만의서점" in msgs[0]["content"]
    assert "민지" not in msgs[-1]["content"]
    assert msgs[-1]["content"] == reply_anchor(last)
    assert "이 문장에만 답하라" in msgs[-1]["content"]


def test_early_farewell_is_rewritten_once(personas: tuple[PersonaResponse, PersonaResponse]) -> None:
    """마지막 전이 아닌 작별은 한 번 다시 쓰고, 두 번째도 작별이면 그 줄을 남긴다."""
    p_b = personas[1]
    calls = {"n": 0}

    async def llm(messages: list[dict[str, str]]) -> str:
        calls["n"] += 1
        assert "EARLY-FAREWELL" in messages[-1]["content"] if calls["n"] == 2 else True
        if calls["n"] == 1:
            return '{"text": "네 들어가세요 ㅎㅎ 나중에 봐요"}'
        return '{"text": "네 들어가세요 ㅎㅎ 또 봐요"}'

    async def _run() -> None:
        result = await speak_as_b(
            persona=p_b,
            nickname_self="준호",
            nickname_other="민지",
            transcript=[("a", "오늘 얘기 즐거웠어요")],
            closing_hint="none",
            llm=llm,
            index=11,
            turns=15,
        )
        assert calls["n"] == 2
        assert result.text == "네 들어가세요 ㅎㅎ 또 봐요"
        assert result.reason is None

    asyncio.run(_run())


def test_phase_hints_do_not_reuse_closing_sentences() -> None:
    """앞·중간·뒤 힌트가 마지막 왕복 문장과 겹치지 않는다."""
    for hint in (HINT_OPEN, HINT_MIDDLE, HINT_LATE):
        assert HINT_NO_NEW_QUESTION not in hint
        assert HINT_CLOSE not in hint


def test_address_line_quotes_nickname_so_it_has_no_trailing_da():
    """닉네임 바로 뒤에 '다'를 붙이면 모델이 '셰일다님'처럼 이름의 일부로 베낀다 (대조 실험 24/24)."""
    from app.features.simulation_migration.prompts import address_line

    line = address_line("셰일")
    assert '상대의 닉네임: "셰일".' in line
    assert "셰일다" not in line
