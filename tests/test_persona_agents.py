import asyncio
import json

import pytest

from app.features.persona import agents
from app.features.persona.agents import SYSTEM_PROMPT, ConversationAgent
from app.features.persona.schemas import Topic

REACT = "직전 사용자 답변의 구체적인 내용 하나에 먼저 반응"
INTRO = "안녕하세요, 저는 하루예요."


def _topic(**kw) -> Topic:
    base = dict(
        id="t",
        weight="core",
        intent="취미 파악",
        seed="약속 없는 주말은 보통 어떻게 보내세요?",
        covers=(),
        opener="저는 늦잠부터 자는 편이에요",
    )
    base.update(kw)
    return Topic(**base)


def test_first_turn_intro_reason_and_no_reaction():
    text = ConversationAgent._instruction(_topic(), 0, 5, "민수")
    assert REACT not in text
    assert "이전 답변은 아직 없습니다" in text
    assert INTRO in text
    assert "민수님을 알아가고 싶어서" in text


def test_first_turn_order_question_then_haru_answer_then_invite():
    text = ConversationAgent._instruction(_topic(), 0, 5, "민수")
    intro = text.index(INTRO)
    reason = text.index("알아가고 싶어서")
    question = text.index("이번 주제의 질문")
    seed = text.index("약속 없는 주말은")
    answer = text.index("하루 자신의 예시 답변을 한 문장으로 덧붙이기")
    invite = text.index("편하게 답해 달라고")
    assert intro < reason < question < answer < invite
    assert question < seed < answer
    assert "저는 늦잠부터 자는 편이에요" in text
    assert "하루 자신의 발화" in text


def test_later_turn_keeps_reaction_instruction():
    text = ConversationAgent._instruction(_topic(), 1, 5, "민수")
    assert REACT in text
    assert INTRO not in text
    assert "이전 답변은 아직 없습니다" not in text


def test_choices_preserved_on_first_and_later_turns():
    topic = _topic(choices=("집", "밖"))
    for i in (0, 2):
        text = ConversationAgent._instruction(topic, i, 5, "민수")
        assert "집 / 밖" in text
        assert REACT not in text
    assert INTRO in ConversationAgent._instruction(topic, 0, 5, "민수")


def test_system_prompt_first_turn_exception():
    assert "첫 턴은 예외" in SYSTEM_PROMPT


# ── segments ─────────────────────────────────────────────

FIRST_TYPES = ["intro", "reason", "question", "self_disclosure", "answer_prompt"]


def _generate(monkeypatch, turn_index, reply=None, error=False):
    import asyncio

    from app.features.persona import agents

    async def fake_call(**kwargs):
        if error:
            raise agents.LLMError("boom")
        return reply

    monkeypatch.setattr(agents, "_call", fake_call)
    return asyncio.run(
        ConversationAgent().generate(history=[], topic=_topic(), turn_index=turn_index, total_turns=5, nickname="민수")
    )


def _valid_parts(**over):
    parts = {
        "intro": INTRO,
        "reason": "민수님을 알아가고 싶어서 이야기해 보려고요.",
        "question": "주말엔 보통 뭐 하세요?",
        "self_disclosure": "저는 늦잠부터 자는 편이에요.",
        "answer_prompt": "편하게 답해 주세요.",
    }
    parts.update(over)
    return parts


def test_first_turn_llm_segments_typed_in_order(monkeypatch):
    import json

    parts = _valid_parts()
    u = _generate(monkeypatch, 0, reply=json.dumps(parts, ensure_ascii=False))
    assert [s.type for s in u.segments] == FIRST_TYPES
    assert [s.text for s in u.segments] == [parts[t] for t in FIRST_TYPES]
    assert u.text == " ".join(parts[t] for t in FIRST_TYPES)
    assert u.source == "llm"


def test_first_turn_bad_json_or_missing_key_falls_back_to_template(monkeypatch):
    for reply in ("그냥 자유 텍스트\n줄바꿈", '{"intro": "안녕"}'):
        u = _generate(monkeypatch, 0, reply=reply)
        assert [s.type for s in u.segments] == FIRST_TYPES
        assert u.segments[0].text == INTRO
        assert u.segments[2].text == _topic().seed
        assert u.source == "seed"


def test_first_turn_llm_error_falls_back(monkeypatch):
    u = _generate(monkeypatch, 0, error=True)
    assert [s.type for s in u.segments] == FIRST_TYPES
    assert "민수님" in u.segments[1].text


def test_later_turn_segments_do_not_guess_structure(monkeypatch):
    u = _generate(monkeypatch, 1, reply="좋네요.\n그럼 주말엔요?")
    assert [(s.type, s.text) for s in u.segments] == [("message", "좋네요.\n그럼 주말엔요?")]
    u = _generate(monkeypatch, 1, error=True)
    assert [(s.type, s.text) for s in u.segments] == [("question", _topic().seed)]


def _assert_template(u):
    assert [s.type for s in u.segments] == FIRST_TYPES
    assert u.source == "seed"
    assert u.segments[0].text == INTRO
    assert u.segments[2].text == _topic().seed
    assert u.text == " ".join(s.text for s in u.segments)


def test_first_turn_semantic_violations_fall_back_to_full_template(monkeypatch):
    import json

    bad = [
        _valid_parts(intro="안녕하세요, 하루입니다."),
        _valid_parts(reason="가볍게 이야기해 보려고요."),  # nickname 없음
        _valid_parts(reason="민수님, 오늘 어때요."),  # 알아가고 싶다는 의미 없음
        _valid_parts(question="주말엔 보통 뭐 하세요"),  # 물음표 없음
        _valid_parts(question="주말엔 뭐 하세요? 평일은요?"),  # 복수 물음표
        _valid_parts(answer_prompt="민수님은 어떠세요?"),  # 두 번째 질문
        _valid_parts(self_disclosure="저는 늦잠 자요. 민수님은요?"),
        _valid_parts(reason="민수님을 알아가고 싶은데 괜찮죠?"),
        _valid_parts(intro="   "),  # 공백
        _valid_parts(question=5),  # 타입 오류
    ]
    for parts in bad:
        _assert_template(_generate(monkeypatch, 0, reply=json.dumps(parts, ensure_ascii=False)))


def test_segment_rejects_blank_text():
    import pytest
    from pydantic import ValidationError

    from app.features.persona.schemas import Segment

    for text in ("", "   ", "\n\t"):
        with pytest.raises(ValidationError):
            Segment(type="message", text=text)
    assert Segment(type="message", text="  안녕  ").text == "안녕"


def _parse_json(monkeypatch, raw):
    """_call(LLM 호출 경계)만 가짜로 — JSON 추출은 진짜 _call_json 이 한다."""

    async def fake_call(**kwargs):
        return raw

    monkeypatch.setattr(agents, "_call", fake_call)
    return asyncio.run(agents._call_json(system="", messages=[], max_tokens=1, timeout=1))


@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```JSON\n{"a": 1}\n```',
        '결과입니다:\n```json\n{"a": 1}\n```',
        '다음과 같아요.\n{"a": 1}\n이상입니다.',
    ],
)
def test_json_is_extracted_from_common_llm_wrappings(monkeypatch, raw):
    assert _parse_json(monkeypatch, raw) == {"a": 1}


def test_non_json_reply_is_llm_error(monkeypatch):
    with pytest.raises(agents.LLMError):
        _parse_json(monkeypatch, "죄송해요, 지금은 답할 수 없어요.")


def test_tagging_keeps_tags_when_llm_wraps_json_in_uppercase_fence(monkeypatch):
    async def fake_call(**kwargs):
        return '```JSON\n{"primary": ["contact_rhythm", "made_up"], "secondary": [], "off_topic": false}\n```'

    monkeypatch.setattr(agents, "_call", fake_call)

    tags = asyncio.run(agents.TaggingAgent().tag("연락은 자주 하는 편이세요?", "하루에 한두 번이면 충분해요"))

    assert tags is not None
    assert tags.primary == ["contact_rhythm"]


def _extract(monkeypatch, summaries):
    """추출 LLM 이 점수·서술과 함께 summaries 를 보냈을 때. _call 만 가짜."""
    payload = {
        "avoidance": 78,
        "seriousness": 90,
        "narrative": {"headline": "h", "body": "b", "traits": []},
        "summaries": summaries,
    }

    async def fake_call(**kwargs):
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr(agents, "_call", fake_call)
    return asyncio.run(agents.ExtractionAgent().extract([{"role": "user", "content": "러닝해요"}]))


def _card(category, title="천천히 가까워지는 편"):
    return {"category": category, "title": title, "content": "서서히 알아가는 걸 편하게 느껴요"}


def test_overlong_summary_card_is_dropped_without_losing_the_extraction(monkeypatch):
    raw = _extract(monkeypatch, [_card("intimacy", title="가" * 41), _card("orientation")])

    assert (raw.avoidance, raw.seriousness) == (78, 90)  # 점수·서술은 그대로
    assert [s.category for s in raw.summaries] == ["orientation"]  # 형식을 어긴 카드만 버린다


def test_too_many_summary_cards_do_not_fail_the_extraction(monkeypatch):
    areas = ["intimacy", "communication", "conflict", "ideal", "orientation", "intimacy"]

    raw = _extract(monkeypatch, [_card(a) for a in areas])

    assert raw.avoidance == 78
    assert len(raw.summaries) == 6  # 중복 정리는 service(valid_summaries) 몫


@pytest.mark.parametrize("value", ["카드 없음", 3, {"category": "intimacy"}, None], ids=["str", "int", "dict", "null"])
def test_malformed_summaries_value_is_ignored(monkeypatch, value):
    raw = _extract(monkeypatch, value)

    assert raw.avoidance == 78
    assert raw.summaries == []


# ── 기록에 없는 과거 언급 ─────────────────────────────────


@pytest.mark.parametrize(
    "reply",
    [
        "저번에 러닝 좋아한다고 하셨잖아요. 주말엔 뭐 하세요?",
        "지난번에 말씀하신 거 기억나요 ㅎㅎ 주말엔요?",
        "저번에 뵀을 때 인상이 좋으셨어요. 주말엔 뭐 하세요?",
    ],
)
def test_reply_assuming_past_meeting_falls_back_to_seed(monkeypatch, reply):
    out = _generate(monkeypatch, turn_index=2, reply=reply)

    assert out.source == "seed"
    assert out.text == _topic().seed


@pytest.mark.parametrize(
    "reply",
    [
        "아까 노래 들으신다고 하셨죠 ㅎㅎ 주말엔 보통 뭐 하세요?",
        "저번 주말엔 뭐 하셨어요? 저는 늦잠 잤어요 ㅎㅎ",  # 지난 주말을 묻는 정상 질문
        "예전에 봤던 영화 중에 기억에 남는 거 있어요?",
        "예전에 만났던 분이랑 다툴 땐 보통 어떻게 하셨어요?",  # 옛 연애 얘기
    ],
)
def test_reply_without_claiming_past_meeting_is_kept(monkeypatch, reply):
    out = _generate(monkeypatch, turn_index=2, reply=reply)

    assert (out.source, out.text) == ("llm", reply)
