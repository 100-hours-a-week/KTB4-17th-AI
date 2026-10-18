"""합성 라벨 점검 — 시드 대비 구간 일치와 답변 방식별 집계를 확인한다."""

from scripts.check_synthetic_labels import bucket, check_extraction, check_tagging


def _tag(mode, primary, off=False, agreement=1.0, intended=False):
    ctx = {"mode": mode} | ({"intendedOffTopic": True} if intended else {})
    maj = {"primary": primary, "secondary": [], "off_topic": off}
    return {
        "id": f"t-{mode}-{off}",
        "context": ctx,
        "aggregate": {"validSamples": 5, "majority": maj, "agreement": agreement},
    }


def test_bucket_matches_seed_ranges():
    assert (bucket(34), bucket(35), bucket(64), bucket(65)) == ("low", "mid", "mid", "high")


def test_tagging_groups_by_mode():
    rows = [
        _tag("off_topic", [], off=True, intended=True),
        _tag("off_topic", ["routine"], intended=True, agreement=0.4),
        _tag("short", []),
        _tag("normal", ["avoidance"]),
    ]
    r = check_tagging(rows)
    assert r["byMode"]["intended_off_topic"] == {"items": 2, "offTopicRate": 0.5, "emptyPrimaryRate": 0.0}
    assert r["byMode"]["short"]["emptyPrimaryRate"] == 1.0
    assert r["lowAgreementItems"] == 1


def test_extraction_against_seed():
    from app.features.persona.schemas import SCORED

    levels = dict.fromkeys(SCORED, "unanswered") | {"avoidance": "high", "anxiety": "low"}
    scores = dict.fromkeys(SCORED) | {"avoidance": 80, "anxiety": 20}
    seeds = {"s1": {"levels": levels, "scores": scores}}
    rows = [{"id": "synthetic:s1", "output": {"avoidance": 75, "anxiety": 70, "openness": 50}}]
    r = check_extraction(rows, seeds)
    assert r["bucketAgreement"] == 0.5  # avoidance 일치, anxiety 불일치
    assert r["meanAbsError"] == 27.5
    assert r["unknownDimScoredRate"] == round(1 / 13, 4)  # openness 는 시드에서 잘 모름
    assert r["largeMismatches"] == [{"id": "synthetic:s1", "dim": "anxiety", "seed": "low", "teacher": 70}]


def test_check_answers_flags_copied_cues():
    from scripts.check_synthetic_labels import check_answers
    from scripts.generate_synthetic_onboarding import DIMENSION_CUES

    cue = DIMENSION_CUES["withdrawal"]["high"][0]
    sessions = [{"turns": [{"answer": f"저는 {cue}이에요"}, {"answer": "주말엔 수영장 가요"}]}]
    r = check_answers(sessions)
    assert r["cueCopyRate"] == 0.5 and r["answers"] == 2


def test_unknown_leak_is_split_into_direct_and_cross():
    from app.features.persona.schemas import SCORED

    levels = dict.fromkeys(SCORED, "high") | {"anxiety": "unanswered", "engagement": "unanswered"}
    scores = {k: (None if v == "unanswered" else 80) for k, v in levels.items()}
    seeds = {"s1": {"levels": levels, "scores": scores}}

    def tag(primary):
        return {"aggregate": {"majority": {"primary": primary, "off_topic": False}}}

    # t0 slow_reply 는 anxiety 를 겨눈 질문 → direct, t1 receiving_hurt 는 engagement 를 겨누지 않음 → cross
    tags = {"synthetic:s1:t0": tag(["anxiety"]), "synthetic:s1:t1": tag(["engagement"])}
    rows = [{"id": "synthetic:s1", "output": {"anxiety": 70, "engagement": 30}}]
    r = check_extraction(rows, seeds, tags=tags, topics={"s1": ["slow_reply", "receiving_hurt"]})
    assert r["unknownDirectLeakRate"] == 0.5 and r["unknownCrossLeakRate"] == 0.5
