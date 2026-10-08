"""추출 결과를 페르소나에 반영하는 규칙 — DB·LLM 없음."""

from app.features.persona.schemas import CONFIDENCE_LOW
from app.features.persona_extraction.service import blend_scores, clean_phrases


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
    from app.features.persona_extraction.service import update_phrase_weights

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
