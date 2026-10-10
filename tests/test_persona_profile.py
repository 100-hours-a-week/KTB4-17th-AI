"""profile.describe — simulation·practice 프롬프트에 들어가는 페르소나 블록."""

from app.features.persona.profile import describe
from app.features.persona.schemas import ConversationStyle, PersonaResponse


def test_describe_includes_mbti_as_reference_only():
    text = describe("민수", PersonaResponse(persona_id="p", scores={"avoidance": 80}, mbti="INTP"))

    assert "MBTI: INTP" in text
    assert "성향이 우선" in text


def test_describe_omits_mbti_line_when_unknown():
    text = describe("민수", PersonaResponse(persona_id="p", scores={}))

    assert "MBTI" not in text


def test_describe_leaves_out_unknown_dimensions():
    """답하지 않은 차원은 null(모름) — '뚜렷한 성향'에도, 중간 성향으로도 연기되지 않는다."""
    p = PersonaResponse(persona_id="p", scores={"avoidance": 80, "anxiety": None}, confidence={"anxiety": "LOW"})

    text = describe("민수", p)

    assert "거리 두기" in text
    assert "관계 불안" not in text


def test_describe_turns_mbti_into_weak_tone_hints_only():
    """MBTI 는 성향이 아니라 말투로만 — E·F 면 먼저 말 걸고 공감 표현이 섞인 말투를 약하게."""
    text = describe("민수", PersonaResponse(persona_id="p", scores={}, mbti="ENFP"))

    assert "말투 힌트" in text
    assert "먼저 말을 거는 편" in text  # E
    assert "공감" in text  # F
    assert "뚜렷한 성향:\n- (특별히 치우친 성향 없음)" in text  # 성향 목록엔 아무것도 안 들어간다


def test_describe_adds_j_p_as_tone_only():
    """J/P 는 관계 진지도 같은 성향이 아니라 말투로만 — 계획형 말투 / 즉흥형 말투."""
    j = describe("민수", PersonaResponse(persona_id="p", scores={}, mbti="ISTJ"))
    p = describe("민수", PersonaResponse(persona_id="p", scores={}, mbti="ENFP"))

    assert "약속이나 계획 얘기를 구체적으로 꺼내는 편 (J)" in j
    assert "즉흥적인 제안이 섞인 말투 (P)" in p


def test_describe_includes_conversation_style_block():
    p = PersonaResponse(
        persona_id="p1",
        scores={},
        conversation_style=ConversationStyle(
            speech_level="반말",
            frequent_phrases=["오 대박", "아 그니까"],
            laughter="ㅋㅋ를 자주",
            summary="리액션 큰 편",
        ),
    )

    text = describe("민수", p)

    assert "대화 스타일" in text
    assert '"오 대박"' in text and "반말" in text and "ㅋㅋ를 자주" in text and "리액션 큰 편" in text


def test_describe_without_style_has_no_style_block():
    assert "대화 스타일" not in describe("민수", PersonaResponse(persona_id="p1", scores={}))
