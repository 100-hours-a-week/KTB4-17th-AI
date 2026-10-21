"""규칙 검사 8종(양성·음성) + 분기 판정 + LLM 심사 파싱."""

from __future__ import annotations

import asyncio

from app.v2.simulation.schemas import Issue, LLMResult, ScriptLineV2, ToolCall, TopicFact, TopicPlan
from app.v2.simulation.verifier import judge, route, rule_check
from tests.v2.sim_fixtures import FakeLLM, ctx, good_script, judge_flag, judge_pass

PLAN = TopicPlan(
    mode="shared",
    facts_a=[TopicFact(ref="A-f1", fact_id=1, owner="a", category="취미", text="주말마다 산에 간다")],
    facts_b=[TopicFact(ref="B-f1", fact_id=11, owner="b", category="취미", text="트레킹 동호회 활동")],
)


def _types(issues):
    return [(x.i, x.type, x.severity) for x in issues]


def _with(script, i, **kw):
    script = [x.model_copy() for x in script]
    script[i] = script[i].model_copy(update=kw)
    return script


def test_good_script_passes():
    issues, warnings = rule_check(good_script(), ctx(), PLAN, 10)
    assert issues == [] and warnings == []
    assert route(issues, 20) == "pass"


def test_format_wrong_line_count_and_order():
    script = good_script()[:-1]
    issues, _ = rule_check(script, ctx(), PLAN, 10)
    assert ("format", "structural") in {(x.type, x.severity) for x in issues}
    assert route(issues, len(script)) == "regenerate"

    swapped = _with(good_script(), 0, speaker="b")
    issues, _ = rule_check(swapped, ctx(), PLAN, 10)
    assert any(x.type == "format" and x.i == 0 for x in issues)


def test_empty_script_is_structural():
    issues, warnings = rule_check([], ctx(), PLAN, 10)
    assert _types(issues) == [(None, "format", "structural")]
    assert warnings == []


def test_ownership_partner_fact_before_partner_mentions():
    # a 가 b 의 fact(트레킹 동호회)를 b 가 꺼내기 전에 말함
    script = _with(good_script(), 2, text="저는 트레킹 동호회 활동을 해요.", fact_refs=["B-f1"])
    issues, _ = rule_check(script, ctx(), PLAN, 10)
    assert (2, "ownership", "local") in _types(issues)


def test_ownership_allowed_after_partner_mentions():
    script = _with(good_script(), 1, fact_refs=["B-f1"])  # b 가 먼저 꺼냄
    script = _with(script, 2, fact_refs=["B-f1", "A-f1"])  # a 가 질문·공감으로 참조
    issues, _ = rule_check(script, ctx(), PLAN, 10)
    assert not [x for x in issues if x.type == "ownership"]


def test_ownership_unknown_ref():
    script = _with(good_script(), 4, fact_refs=["A-f9"])
    issues, _ = rule_check(script, ctx(), PLAN, 10)
    assert (4, "ownership", "local") in _types(issues)


def test_self_address_with_boundary():
    script = _with(good_script(), 2, text="민수님은 주말에 뭐 하세요?")  # a(민수)가 자기를 부름
    issues, _ = rule_check(script, ctx(), PLAN, 10)
    assert (2, "self_address", "local") in _types(issues)

    # 다른 이름의 일부면 자기 호칭이 아니다 (#77)
    c = ctx(name_a="셰일", name_b="내가진짜셰일")
    script = _with(good_script(), 2, text="내가진짜셰일님은 주말에 뭐 하세요?")
    issues, _ = rule_check(script, c, PLAN, 10)
    assert not [x for x in issues if x.type == "self_address"]


def test_echo_previous_partner_line():
    script = good_script()
    script = _with(script, 3, text=script[2].text)
    issues, _ = rule_check(script, ctx(), PLAN, 10)
    assert (3, "echo", "local") in _types(issues)


def test_echo_ignores_short_reaction():
    script = _with(good_script(), 1, text="네 맞아요 ㅎㅎ")
    script = _with(script, 2, text="네 맞아요 ㅎㅎ")
    issues, _ = rule_check(script, ctx(), PLAN, 10)
    assert not [x for x in issues if x.type == "echo"]


def test_early_farewell_only_before_last_round():
    script = _with(good_script(), 6, text="오늘 즐거웠어요, 조심히 가세요!")
    issues, _ = rule_check(script, ctx(), PLAN, 10)
    assert (6, "early_farewell", "local") in _types(issues)
    # 마지막 왕복(18, 19)의 작별은 정상
    issues, _ = rule_check(good_script(), ctx(), PLAN, 10)
    assert not [x for x in issues if x.type == "early_farewell"]


def test_perspective_violation_from_guardrail():
    # b 의 관심사("독서 모임")를 a 가 자기 것처럼 말함
    script = _with(good_script(), 2, text="저는 독서 모임을 정말 좋아해요.")
    issues, _ = rule_check(script, ctx(), PLAN, 10)
    assert (2, "perspective", "local") in _types(issues)


def test_low_effort_is_warning_only():
    script = good_script()
    script = [x if x.i < 14 else x.model_copy(update={"text": "네"}) for x in script]
    issues, warnings = rule_check(script, ctx(), PLAN, 10)
    assert "low_effort" in warnings
    assert not [x for x in issues if x.type == "low_effort"]


def test_route_regenerate_when_many_local_lines():
    issues = [Issue(i=k, type="echo", severity="local") for k in range(7)]
    assert route(issues, 20) == "regenerate"  # 7 > 20 * 0.3
    assert route(issues[:6], 20) == "repair"
    assert route([Issue(type="low_effort", severity="warning")], 20) == "pass"


def test_judge_full_parses_issues_and_drops_out_of_range():
    llm = FakeLLM(
        {
            "judge": [
                LLMResult(
                    calls=[
                        ToolCall(name="report_issue", arguments={"i": 5, "type": "judge_mixup", "evidence": "x"}),
                        ToolCall(name="report_issue", arguments={"i": 99, "type": "judge_mixup", "evidence": "x"}),
                        ToolCall(name="report_issue", arguments={"i": 3, "type": "ownership", "evidence": "x"}),
                        ToolCall(name="finish_review", arguments={"pass": False}),
                    ]
                )
            ]
        }
    )
    res = asyncio.run(judge(llm, good_script(), ctx(), PLAN, focus=None, timeout=5))
    assert _types(res.issues) == [(5, "judge_mixup", "local")]
    assert res.passed is False and res.skipped is False
    assert "전체 대본" in llm.calls[0]["user"]


def test_judge_focus_only_shows_nearby_lines_and_filters():
    llm = FakeLLM({"judge": [judge_flag(15)]})
    res = asyncio.run(judge(llm, good_script(), ctx(), PLAN, focus={5}, timeout=5))
    assert res.issues == []  # 15 는 5±2 범위 밖
    user = llm.calls[0]["user"]
    assert "\n3. " in user and "\n7. " in user and "\n8. " not in user and "\n2. " not in user


def test_judge_pass():
    res = asyncio.run(judge(FakeLLM({"judge": [judge_pass()]}), good_script(), ctx(), PLAN, focus=None, timeout=5))
    assert res.passed and not res.issues


def test_judge_error_skips():
    llm = FakeLLM({"judge": [RuntimeError("boom")]})
    res = asyncio.run(judge(llm, good_script(), ctx(), PLAN, focus=None, timeout=5))
    assert res.skipped and res.passed and res.issues == []


def test_line_model_rejects_empty_text():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ScriptLineV2(i=0, speaker="a", text="")


def test_self_address_with_ssi_and_identity_swap():
    """실제 모델 출력 회귀: a(민수)가 "안녕하세요, 민수 씨. 지은입니다." 라고 말함."""
    script = _with(good_script(), 0, text="안녕하세요, 민수 씨. 지은입니다.")
    issues, _ = rule_check(script, ctx(), PLAN, 10)
    assert (0, "self_address", "local") in _types(issues)

    script = _with(good_script(), 2, text="반가워요, 저는 지은이에요.")
    issues, _ = rule_check(script, ctx(), PLAN, 10)
    assert (2, "self_address", "local") in _types(issues)

    # 상대 이름을 부르는 건 정상
    script = _with(good_script(), 2, text="지은 씨는 주말에 뭐 하세요? 저는 민수예요.")
    issues, _ = rule_check(script, ctx(), PLAN, 10)
    assert not [x for x in issues if x.type == "self_address"]


def test_early_farewell_extra_expressions():
    for text in ("네, 좋아요! 그럼 다음 주말에 봬요.", "오늘 대화 즐거웠어요.", "안녕히 가세요"):
        script = _with(good_script(), 8, text=text)
        issues, _ = rule_check(script, ctx(), PLAN, 10)
        assert (8, "early_farewell", "local") in _types(issues), text


def test_judge_legacy_and_dedup():
    """예전 도구 이름도 받고, 같은 줄·같은 type 지적은 하나로 합친다."""
    legacy = LLMResult(
        calls=[
            ToolCall(name="report_issue", arguments={"i": 4, "type": "judge_tone", "evidence": "a"}),
            ToolCall(name="report_issue", arguments={"i": 4, "type": "judge_tone", "evidence": "b"}),
            ToolCall(name="finish_review", arguments={"pass": False}),
        ]
    )
    res = asyncio.run(judge(FakeLLM({"judge": [legacy]}), good_script(), ctx(), PLAN, focus=None, timeout=5))
    assert _types(res.issues) == [(4, "judge_tone", "local")]


def test_ownership_partner_said_it_without_tag_is_not_issue():
    """실측 오탐: b 가 문장으로 트레킹을 말했지만 fact_refs 를 빠뜨림 → a 의 B-f1 참조는 정상."""
    script = _with(good_script(), 1, text="저는 트레킹 동호회 활동을 해요, 한 달에 두 번쯤 가요.")
    script = _with(script, 2, text="트레킹 동호회라니 멋져요. 저도 산을 좋아해요.", fact_refs=["B-f1", "A-f1"])
    issues, _ = rule_check(script, ctx(), PLAN, 10)
    assert not [x for x in issues if x.type == "ownership"]


def test_ownership_mistag_is_not_issue_but_real_claim_is():
    # b 가 자기 얘기를 하며 A-f1 을 잘못 붙임 (내용은 겹치지 않음) → 표기 실수
    mistag = _with(good_script(), 1, text="저는 요즘 에세이를 많이 읽어요.", fact_refs=["A-f1"])
    issues, _ = rule_check(mistag, ctx(), PLAN, 10)
    assert not [x for x in issues if x.type == "ownership"]
    # b 가 a 의 fact 를 자기 것처럼 말함 (내용이 겹침) → 혼동
    claim = _with(good_script(), 1, text="저도 주말마다 산에 간다니까요, 등산 좋아해요.", fact_refs=["A-f1"])
    issues, _ = rule_check(claim, ctx(), PLAN, 10)
    assert (1, "ownership", "local") in _types(issues)


# ── Grok 반례 검토 회귀 (2026-10-19) ──


def test_judge_string_line_number_is_kept():
    res_obj = LLMResult(
        calls=[
            ToolCall(
                name="submit_review",
                arguments={"issues": [{"i": "7", "type": "judge_mixup", "evidence": "x"}], "pass": False},
            )
        ]
    )
    res = asyncio.run(judge(FakeLLM({"judge": [res_obj]}), good_script(), ctx(), PLAN, focus=None, timeout=5))
    assert _types(res.issues) == [(7, "judge_mixup", "local")] and res.passed is False


def test_judge_fail_without_readable_issue_is_skipped_not_pass():
    res_obj = LLMResult(calls=[ToolCall(name="submit_review", arguments={"issues": [{"i": "x"}], "pass": False})])
    res = asyncio.run(judge(FakeLLM({"judge": [res_obj]}), good_script(), ctx(), PLAN, focus=None, timeout=5))
    assert res.skipped is True


def test_self_claim_with_partner_honorific_in_same_sentence():
    """가드레일은 같은 문장에 '님은'이 있으면 관점 규칙을 끈다 — 자체 규칙이 잡는다."""
    script = _with(good_script(), 2, text="저는 독서 모임을 좋아하는데 지은님은 어떤 책을 읽으세요?")
    issues, _ = rule_check(script, ctx(), PLAN, 10)
    # 질문 문장이 섞여 있어도 앞 문장의 자기 진술을 본다
    script = _with(good_script(), 2, text="저는 독서 모임을 좋아해요. 지은님은 어떤 책을 읽으세요?")
    issues, _ = rule_check(script, ctx(), PLAN, 10)
    assert (2, "perspective", "local") in _types(issues)
    # 부정·질문은 자기 진술이 아니다 (자체 규칙 기준. v1 가드레일은 부정문도 잡을 수 있다 — v1 동작이라 수정하지 않음)
    from app.v2.simulation.verifier import _self_claims

    assert _self_claims("저는 독서 모임은 안 해 봤어요.", ["독서 모임"]) is None
    assert _self_claims("저도 독서 모임 해 보고 싶어요.", ["독서 모임"]) is None
    assert _self_claims("독서 모임 좋아하세요?", ["독서 모임"]) is None


def test_fabricated_job_age_region():
    for text in ("저는 판교에서 일해요.", "저는 스물아홉이고 29살이에요.", "저는 부산에 살아요."):
        script = _with(good_script(), 2, text=text)
        issues, _ = rule_check(script, ctx(), PLAN, 10)
        assert (2, "fabrication", "local") in _types(issues), text
    # 회사 같은 일반 단어는 창작이 아니다
    script = _with(good_script(), 2, text="저는 회사에서 일해요.")
    issues, _ = rule_check(script, ctx(), PLAN, 10)
    assert not [x for x in issues if x.type == "fabrication"]


def test_one_char_nickname_and_same_nickname():
    c = ctx(name_a="민", name_b="지")
    script = _with(good_script(), 2, text="민님은 주말에 뭐 해요?")
    issues, _ = rule_check(script, c, PLAN, 10)
    assert (2, "self_address", "local") in _types(issues)
    same = ctx(name_a="민수", name_b="민수")
    script = _with(good_script(), 2, text="저는 민수입니다. 민수님은요?")
    issues, _ = rule_check(script, same, PLAN, 10)
    assert not [x for x in issues if x.type == "self_address"]


def test_json_debris_in_text_is_structural():
    script = _with(good_script(), 3, text='요리 진짜 재밌는 것 같아. },{fact_refs:["B-f1"]')
    issues, _ = rule_check(script, ctx(), PLAN, 10)
    assert (3, "format", "structural") in _types(issues)


def test_me_too_agreement_is_not_self_claim():
    from app.v2.simulation.verifier import _self_claims

    # 자기 fact 에 근거(트레킹·산에)가 있으면 혼동이 아니다
    own = "트레킹 동호회에서 한 달에 두 번 정도 산에 간다"
    assert _self_claims("나도 등산 좋아해! 동호회에서 산에 가.", ["등산"], own) is None
    assert _self_claims("저는 등산을 매주 가요.", ["등산"]) == "등산"


def test_me_too_preference_claim_of_partner_attr():
    """실측 v2 혼동: b 프로필에 없는 민수의 '주말 캠핑'을 "저도 캠핑 되게 좋아해요"로 자기 것처럼 말함."""
    from app.v2.simulation.verifier import _self_claims

    assert _self_claims("캠핑! 저도 캠핑 되게 좋아해요ㅎㅎ", ["주말 캠핑"]) == "캠핑"
    assert _self_claims("캠핑! 나도 캠핑 가서 불멍하면 마음 편해지더라 ㅋㅋ", ["주말 캠핑"]) == "캠핑"
    assert _self_claims("나는 요리하는 것보다 베이킹이 더 좋아.", ["요리"]) is None  # 비교는 제외
    assert _self_claims("나도 한번 가보고 싶었는데.", ["주말 캠핑"]) is None  # 희망은 제외
    assert _self_claims("저도 주말엔 집에서 쉬어요.", ["주말 캠핑"]) is None  # '주말'만으로는 잡지 않음


def test_fact_keys_and_bare_listing_claim():
    """오탐률 정리 문서(2026-10-19)의 개선 2·3·4번 회귀."""
    from app.v2.simulation.verifier import _fact_keys, _self_claims

    assert _fact_keys("사용자는 요즘 파스타 요리에 빠져 있다.") == ["파스타", "요리"]
    assert "하면" not in _fact_keys("민수님은 캠핑 가서 불멍을 하면 마음이 편안해진다.")
    # 상대 fact 핵심어를 자기 선호로 단정
    assert (
        _self_claims("저도 파스타 정말 좋아해요 ㅎㅎ", [], "", ["사용자는 요즘 파스타 요리에 빠져 있다."]) == "파스타"
    )
    # 주어 없는 선호 단정 — 자기 취향 나열 맥락에서만
    listing = "네! 전시회 가는 것도 좋아하고, 브런치 먹으러 가는 것도 좋아해요. 산책도 좋구요 ㅎㅎ"
    assert _self_claims(listing, ["산책"]) == "산책"
    assert _self_claims("산책 좋아요! 그럼 몇 시쯤 만날까요?", ["산책"]) is None
    # 상대 추측·ㅎㅎ 경계
    assert _self_claims("집에서 요리하는 거 좋아하나봐. 나도 베이킹 하는 거 좋아해.", ["요리"]) is None
    assert _self_claims("캠핑 가서 요리하는 거 재밌겠다 ㅎㅎ 저는 베이킹 하는 걸 좋아해서요!", ["요리"]) is None


def test_near_claim_patterns():
    from app.v2.simulation.verifier import _self_claims

    assert _self_claims("맛집! 나도 좋아해ㅋㅋ", ["맛집 탐방"]) == "맛집"
    assert _self_claims("산책이나 전시회 가는 거 좋아하는데 ㅎㅎ", ["산책"]) == "산책"
    # "맛집도 좋고 …" 처럼 '좋고'로 나열하는 경우는 맞장구 오탐("브런치도 좋고 전시회도 좋죠")과 구분이 안 돼 잡지 않는다
    assert _self_claims("민수님은 등산이랑 요리 좋아하신다고 들었어요.", ["요리"]) is None
    # 맞장구·상대에 대한 말은 제외
    assert _self_claims("산책 좋아요! 그럼 몇 시쯤 만날까요?", ["산책"]) is None
    assert _self_claims("요리하는 거 좋아하는구나!", ["요리"]) is None
    assert _self_claims("민수님은 맛집 탐방 좋아하세요?", ["맛집 탐방"]) is None
    assert _self_claims("맛집 탐방도 좋죠 ㅎㅎ", ["맛집 탐방"]) is None
