"""추출 결과를 페르소나에 반영하는 규칙 — DB·LLM 없음."""

from app.features.persona.schemas import CONFIDENCE_LOW
from app.features.persona_extraction.service import blend_scores, clean_phrases, ground_style, update_phrase_weights


def test_clean_phrases_drops_pii_names_long_and_duplicates():
    phrases = ["오 대박", "010-1234-5678", "민수야 뭐해", "a@b.com", "가" * 31, "오 대박", " 진짜? ", "https://x.y"]

    assert clean_phrases(phrases, banned={"민수"}) == ["오 대박", "진짜?"]


def test_clean_phrases_keeps_at_most_ten():
    assert len(clean_phrases([f"말{chr(0xAC00 + i)}" for i in range(15)], banned=set())) == 10


def test_blend_weights_existing_and_observed():
    scores, conf = blend_scores({"disclosure": 70}, {"disclosure": "HIGH"}, {"disclosure": 40}, base_weight=0.7)

    assert scores["disclosure"] == 61
    assert conf["disclosure"] == "HIGH"


def test_blend_fills_unknown_with_observed_as_low_confidence():
    scores, conf = blend_scores({"openness": None}, {"openness": CONFIDENCE_LOW}, {"openness": 60}, base_weight=0.7)

    assert scores["openness"] == 60
    assert conf["openness"] == CONFIDENCE_LOW


def test_blend_leaves_unobserved_and_non_reflected_dimensions_alone():
    base = {"disclosure": 70, "avoidance": 20}
    scores, _ = blend_scores(base, {}, {"disclosure": None, "avoidance": 90}, base_weight=0.7)

    assert scores == base


def test_update_phrase_weights_new_phrases_get_initial_weight():
    from app.features.persona_extraction.service import update_phrase_weights

    current = {"endings": ["~요", "~음"], "frequent_phrases": ["감사합니다"]}
    phrases, weights = update_phrase_weights(current, previous_weights=None, previous_phrases=None)

    assert phrases["endings"] == ["~요", "~음"]
    assert phrases["frequent_phrases"] == ["감사합니다"]
    assert weights["~요"] == 0.65  # 1순위 보너스 0.05
    assert weights["~음"] == 0.60
    assert weights["감사합니다"] == 0.65


def test_update_phrase_weights_decay_and_eviction():
    from app.features.persona_extraction.service import update_phrase_weights

    # 기존: "~요" (0.8), "~구" (0.6), "~당" (0.22), "~잉" (기록 없음)
    # 새 대화: "~요", "~음" 만 사용 ("~구", "~당", "~잉" 미사용)
    prev_w = {"~요": 0.8, "~구": 0.6, "~당": 0.22}
    prev_p = {"endings": ["~요", "~구", "~당", "~잉"]}
    current = {"endings": ["~요", "~음"]}

    phrases, weights = update_phrase_weights(current, previous_weights=prev_w, previous_phrases=prev_p)

    # "~요": 0.8 * 0.7 + 0.35 + 0.05 = 0.96 (상승)
    # "~음": 신규 0.60
    # "~구": 0.6 * 0.7 = 0.42 (충분히 관찰됐던 표현이라 감쇠하며 3위로 유지)
    # "~당": 0.22 는 0.5 미만이라 검증할 수 없어 즉시 퇴출, "~잉" 은 가중치 기록이 없어 즉시 퇴출
    assert phrases["endings"] == ["~요", "~음", "~구"]
    assert "~당" not in phrases["endings"]
    assert "~당" not in weights
    assert "~잉" not in phrases["endings"]
    assert weights["~구"] == 0.42
    assert weights["~요"] == 0.96


def test_update_phrase_weights_evicts_lowest_when_limit_exceeded():
    from app.features.persona_extraction.service import update_phrase_weights

    # 기존 10개 보유 (각 0.55 — 감쇠 후에도 새 표현보다 낮아 밀려난다)
    old_endings = [f"~어{i}" for i in range(10)]
    prev_w = {e: 0.55 for e in old_endings}
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


def test_update_phrase_weights_evicts_unverifiable_previous_phrase():
    # 가중치가 낮거나 기록이 없는 이전 표현은 이번 대화에 안 나오면 바로 빠진다 (검증 도입 전 데이터 포함)
    prev_p = {"frequent_phrases": ["낙낙", "기록없음", "충분히쓴말"]}
    prev_w = {"낙낙": 0.45, "충분히쓴말": 0.8}
    phrases, weights = update_phrase_weights({"frequent_phrases": []}, prev_w, prev_p)
    assert phrases["frequent_phrases"] == ["충분히쓴말"]
    assert weights == {"충분히쓴말": 0.56}


def test_clean_phrases_drops_profanity():
    assert clean_phrases(["ㅅㅂ", "이새끼", "새끼 고양이", "헐"], banned=set()) == ["새끼 고양이", "헐"]
