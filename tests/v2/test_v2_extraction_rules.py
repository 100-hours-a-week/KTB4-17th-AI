"""추출 결과를 페르소나에 반영하는 규칙 — DB·LLM 없음."""

from app.features.persona.schemas import CONFIDENCE_LOW
from app.v2.persona_extraction.schemas import ScoreObservation
from app.v2.persona_extraction.service import (
    blend_observed,
    build_transcript,
    clean_phrases,
    confidence_from_evidence,
    ground_style,
    update_phrase_weights,
    validate_observations,
)


def test_clean_phrases_drops_pii_names_long_and_duplicates():
    phrases = ["오 대박", "010-1234-5678", "민수야 뭐해", "a@b.com", "가" * 31, "오 대박", " 진짜? ", "https://x.y"]

    assert clean_phrases(phrases, banned={"민수"}) == ["오 대박", "진짜?"]


def test_clean_phrases_keeps_at_most_ten():
    assert len(clean_phrases([f"말{chr(0xAC00 + i)}" for i in range(15)], banned=set())) == 10


def test_confidence_from_evidence_count():
    assert [confidence_from_evidence(n) for n in (1, 2, 3, 5, 6, 20)] == [
        "LOW",
        "LOW",
        "MEDIUM",
        "MEDIUM",
        "HIGH",
        "HIGH",
    ]


def test_validate_observations_drops_out_of_range_evidence_and_unfounded_scores():
    scores = {
        "disclosure": ScoreObservation(value=70, evidence=[1, 2, 2, 99]),  # 99 는 없는 번호, 중복은 한 건
        "openness": ScoreObservation(value=40, evidence=[99]),  # 근거가 하나도 없다 → 환각
        "avoidance": ScoreObservation(value=20, evidence=[]),
    }

    assert validate_observations(scores, 5) == {"disclosure": (70, 2)}


def test_blend_weights_existing_and_observed():
    scores, conf = blend_observed({"disclosure": 70}, {"disclosure": "HIGH"}, {"disclosure": (40, 1)}, base_weight=0.7)

    assert scores["disclosure"] == 61  # 70*0.7 + 40*0.3
    assert conf["disclosure"] == "HIGH"  # 근거가 적어도 기존 신뢰도는 내리지 않는다


def test_blend_raises_confidence_but_never_lowers_it():
    _, conf = blend_observed(
        {"a": 50, "b": 50, "disclosure": 50},
        {"a": "LOW", "b": "MEDIUM", "disclosure": "LOW"},
        {"a": (50, 6), "b": (50, 1), "disclosure": (50, 3)},
        base_weight=0.7,
    )

    assert conf == {"a": "HIGH", "b": "MEDIUM", "disclosure": "MEDIUM"}


def test_blend_fills_unknown_with_observed_and_evidence_confidence():
    scores, conf = blend_observed(
        {"openness": None}, {"openness": CONFIDENCE_LOW}, {"openness": (60, 4)}, base_weight=0.7
    )

    assert scores["openness"] == 60
    assert conf["openness"] == "MEDIUM"


def test_blend_leaves_unobserved_dimensions_alone():
    base = {"disclosure": 70, "avoidance": 20}
    scores, _ = blend_observed(base, {}, {"anxiety": (90, 3)}, base_weight=0.7)

    assert scores == {**base, "anxiety": 90}


def _lines():
    return [
        {"s": "other", "t": "오래된 상대 말", "id": None},
        {"s": "me", "t": "이미 올린 내 말", "id": None},  # 이전에 반영된 발화 — 맥락만, 번호 없음
        {"s": "other", "t": "뭐해?", "id": None},
        {"s": "me", "t": "나 러닝\n다녀옴", "id": 11},
        {"s": "other", "t": "오 대단", "id": None},
        {"s": "me", "t": "ㅋㅋ 고마워", "id": 12},
    ]


def test_build_transcript_numbers_only_citable_own_utterances_and_keeps_partner_as_context():
    text, ids = build_transcript(_lines(), {11, 12})

    assert text.splitlines() == [
        "상대: 뭐해?",
        "나[1]: 나 러닝 다녀옴",
        "상대: 오 대단",
        "나[2]: ㅋㅋ 고마워",
    ]  # 첫 분석 대상 앞의 대화는 버리고, 줄바꿈은 한 줄로
    assert ids == [11, 12]


def test_build_transcript_marks_previously_reflected_own_lines_as_uncitable():
    lines = [
        {"s": "other", "t": "뭐해?", "id": None},
        {"s": "me", "t": "나 러닝", "id": 11},
        {"s": "me", "t": "이미 올린 내 말", "id": None},
        {"s": "me", "t": "ㅋㅋ 고마워", "id": 13},
    ]

    text, ids = build_transcript(lines, {11, 13})

    assert text.splitlines() == ["상대: 뭐해?", "나[1]: 나 러닝", "나: 이미 올린 내 말", "나[2]: ㅋㅋ 고마워"]
    assert ids == [11, 13]


def test_build_transcript_trims_oldest_lines_first_and_numbers_after_trimming():
    lines = [{"s": "me", "t": f"내 말 {i}", "id": i} for i in range(1, 7)]

    text, ids = build_transcript(lines, set(range(1, 7)), max_chars=40)

    assert ids == [5, 6] and text.splitlines()[0] == "나[1]: 내 말 5"


def test_build_transcript_without_citable_lines_is_empty():
    assert build_transcript(_lines(), {999}) == ("", [])


def test_update_phrase_weights_new_phrases_get_initial_weight():
    from app.v2.persona_extraction.service import update_phrase_weights

    current = {"endings": ["~요", "~음"], "frequent_phrases": ["감사합니다"]}
    phrases, weights = update_phrase_weights(current, previous_weights=None, previous_phrases=None)

    assert phrases["endings"] == ["~요", "~음"]
    assert phrases["frequent_phrases"] == ["감사합니다"]
    assert weights["~요"] == 0.65  # 1순위 보너스 0.05
    assert weights["~음"] == 0.60
    assert weights["감사합니다"] == 0.65


def test_update_phrase_weights_decay_and_eviction():
    from app.v2.persona_extraction.service import update_phrase_weights

    # 기존: "~요" (0.8), "~구" (0.35), "~당" (0.22)
    # 새 대화: "~요", "~음" 만 사용 ("~구", "~당" 미사용)
    prev_w = {"~요": 0.8, "~구": 0.35, "~당": 0.22}
    prev_p = {"endings": ["~요", "~구", "~당"]}
    current = {"endings": ["~요", "~음"]}

    phrases, weights = update_phrase_weights(current, previous_weights=prev_w, previous_phrases=prev_p)

    # "~요": 0.8 * 0.7 + 0.35 + 0.05 = 0.96 (상승)
    # "~음": 신규 0.60
    # "~구": 0.35 * 0.7 = 0.25 (감쇠되었으나 0.20 이상이므로 3위로 유지)
    # "~당": 0.22 * 0.7 = 0.15 (< 0.20 이므로 자동 퇴출 Eviction!)
    assert phrases["endings"] == ["~요", "~음", "~구"]
    assert "~당" not in phrases["endings"]
    assert "~당" not in weights
    assert weights["~구"] == 0.24
    assert weights["~요"] == 0.96


def test_update_phrase_weights_evicts_lowest_when_limit_exceeded():
    from app.v2.persona_extraction.service import update_phrase_weights

    # 기존 10개 보유 (각 0.3의 낮은 가중치)
    old_endings = [f"~어{i}" for i in range(10)]
    prev_w = {e: 0.3 for e in old_endings}
    prev_p = {"endings": old_endings}

    # 새 대화에서 새로운 강력한 어미 2개 등장 ("~요", "~음")
    current = {"endings": ["~요", "~음"]}

    phrases, weights = update_phrase_weights(current, previous_weights=prev_w, previous_phrases=prev_p, limit=10)

    # 상위 10개만 유지되어야 함
    assert len(phrases["endings"]) == 10
    # 새 어미가 상위권에 진입
    assert "~요" in phrases["endings"][:2]
    assert "~음" in phrases["endings"][:2]
    # 가중치가 낮아진 기존 어미 2개는 10위 밖으로 밀려나 퇴출
    evicted = [e for e in old_endings if e not in phrases["endings"]]
    assert len(evicted) == 2
    for e in evicted:
        assert e not in weights


def test_ground_style_keeps_only_phrases_used_at_least_five_times_and_five_percent():
    style = {
        "frequent_phrases": ["낙낙", "없는말", "딱네번"],
        "interjections": ["음", "헐"],
        "slang": [],
    }
    utterances = ["낙낙"] * 5 + ["딱네번"] * 4 + ["음"] * 6 + ["헐"] + ["좋아"] * 84  # 총 100

    out, counts = ground_style(style, utterances)

    assert counts == {"낙낙": 5, "음": 6}
    assert out["frequent_phrases"] == ["낙낙"]  # 5회·5%. 4회와 없는 말은 탈락
    assert out["interjections"] == ["음"]  # 6회·6%. 1회는 탈락


def test_ground_style_requires_the_ratio_too():
    utterances = ["낙낙"] * 5 + ["좋아"] * 195  # 5회지만 2.5%

    assert ground_style({"frequent_phrases": ["낙낙"]}, utterances)[0]["frequent_phrases"] == []


def test_ground_style_count_is_per_utterance_and_endings_only_at_the_end():
    utterances = ["낙낙낙낙낙낙"] + ["요 하고 감"] * 5 + ["밥 먹어요"] * 5 + ["잘 지내요~"] * 5
    style = {"frequent_phrases": ["낙낙"], "endings": ["~요", "~음"]}

    out, _ = ground_style(style, utterances)

    assert out["frequent_phrases"] == []  # 한 발화에 여러 번이어도 1회로 센다
    assert out["endings"] == ["~요"]  # 중간의 '요' 는 안 세고, 끝의 '요~' 는 센다 (10회)


def test_ground_style_drops_marker_descriptions_without_the_marker():
    style = {"punctuation": "물음표를 자주 씀", "laughter": "ㅋㅋ를 거의 매 메시지에", "summary": "요약"}

    assert ground_style(style, ["저두요", "음"])[0]["punctuation"] is None
    assert ground_style(style, ["저두요", "음"])[0]["laughter"] is None
    kept, _ = ground_style(style, ["진짜?", "ㅋㅋ 나도"])
    assert kept["punctuation"] == "물음표를 자주 씀" and kept["laughter"] == "ㅋㅋ를 거의 매 메시지에"
    assert kept["summary"] == "요약"


def test_update_phrase_weights_reflects_counts():
    phrases = {"frequent_phrases": ["낙낙", "자주"], "endings": [], "interjections": [], "slang": []}

    _, w = update_phrase_weights(phrases, {}, counts={"낙낙": 5, "자주": 12})

    assert w["낙낙"] == 0.8  # 0.5+0.05*5 + 첫째 가산 0.05
    assert w["자주"] == 1.0  # 12회는 상한 1.0


def test_update_phrase_weights_with_counts_converges_to_target_for_existing():
    phrases = {"frequent_phrases": ["낙낙"], "endings": [], "interjections": [], "slang": []}

    _, w = update_phrase_weights(phrases, {"낙낙": 1.0}, {"frequent_phrases": ["낙낙"]}, counts={"낙낙": 6})

    assert w["낙낙"] == 0.99  # 1.0*0.7 + 0.8*0.3 + 0.05


def test_update_phrase_weights_without_counts_keeps_old_rule():
    phrases = {"frequent_phrases": ["낙낙"], "endings": [], "interjections": [], "slang": []}

    _, w = update_phrase_weights(phrases, {})

    assert w["낙낙"] == 0.65
