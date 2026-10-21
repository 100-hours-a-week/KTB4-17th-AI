"""합성 온보딩 세션 생성기 — LLM 없이 시드 질문·고정 답으로 흐름 규칙만 확인한다."""

import asyncio
import json
import random

from app.features.persona.schemas import MAX_ANSWER_LEN, SCORED, TOPICS_BY_ID
from app.features.persona.service import ONBOARDING_TOTAL_TURNS, REASK_PREFIX
from scripts.generate_synthetic_onboarding import (
    LEVEL_RANGES,
    FakeSimulator,
    LeakChecker,
    SessionResult,
    TurnRecord,
    clean_answer,
    describe_profile,
    main,
    run_session,
    sample_profile,
    seed_question,
)


def _session(seed: str = "t", **style) -> SessionResult:
    rng = random.Random(seed)
    profile = sample_profile(rng, seed_id="seed-test")
    for key, value in style.items():
        setattr(profile.style, key, value)
    return asyncio.run(
        run_session(profile, rng=rng, ask=seed_question, simulator=FakeSimulator(), total_turns=ONBOARDING_TOTAL_TURNS)
    )


def test_profile_is_deterministic_and_scores_match_levels():
    a = sample_profile(random.Random("x:1"), seed_id="s")
    b = sample_profile(random.Random("x:1"), seed_id="s")
    assert a == b
    assert set(a.levels) == set(SCORED)
    for key, level in a.levels.items():
        if level == "unanswered":
            assert a.scores[key] is None
        else:
            low, high = LEVEL_RANGES[level]
            assert low <= a.scores[key] <= high


def test_profile_description_hides_scores_and_dimension_keys():
    profile = sample_profile(random.Random("d"), seed_id="s")
    text = describe_profile(profile)
    for key in SCORED:
        assert key not in text  # 영문 차원 키가 새면 시뮬레이터가 "avoidance" 같은 말을 할 수 있다
    for score in profile.scores.values():
        if score is not None:
            assert f"{score}점" not in text


def test_session_follows_onboarding_topic_rules():
    result = _session(off_topic_rate=0.0, joke_rate=0.0, short_rate=0.0)
    topics = [t.topicId for t in result.turns]
    assert len(topics) == ONBOARDING_TOTAL_TURNS
    assert len(set(topics)) == len(topics)
    assert TOPICS_BY_ID[topics[-1]].is_closing  # 마지막 턴은 클로징 주제
    assert TOPICS_BY_ID[topics[0]].weight == 0  # 첫 턴은 가벼운 주제
    history = result.history()
    assert len(history) == 2 * len(topics)
    assert [m["role"] for m in history[:2]] == ["assistant", "user"]


def test_off_topic_answer_is_reasked_once_without_consuming_turn():
    result = _session(off_topic_rate=1.0, joke_rate=0.0, short_rate=0.0)
    assert len(result.turns) == ONBOARDING_TOTAL_TURNS
    non_closing = [t for t in result.turns if not TOPICS_BY_ID[t.topicId].is_closing]
    assert all(t.mode == "off_topic" for t in non_closing)
    for t in non_closing:
        assert t.reask["question"].startswith(REASK_PREFIX)
        assert t.reask["offTopicAnswer"] not in [m["content"] for m in result.history()]  # 운영처럼 저장 안 함
        assert t.answer != t.reask["offTopicAnswer"]


def test_clean_answer_strips_labels_and_caps_length():
    assert clean_answer('나: "주말엔 러닝해요"') == "주말엔 러닝해요"
    assert len(clean_answer("가" * 500)) == MAX_ANSWER_LEN
    assert clean_answer(None) == ""
    assert clean_answer("음..\n그냥  덮어두는 편?") == "음.. 그냥 덮어두는 편?"
    long = "주말엔 보통 테니스 쳐요. " * 20
    assert clean_answer(long).endswith("쳐요.")  # 문장 중간에서 자르지 않는다


def test_leak_checker_flags_gold_utterances_only():
    checker = LeakChecker()
    gold = json.loads(
        next(
            line
            for line in open("evals/quality_datasets/persona_onboarding_tagging.jsonl", encoding="utf-8")
            if line.strip()
        )
    )["input"]["answer"]
    assert checker.leaks(gold)
    assert checker.leaks(gold.replace(".", "!"))  # 문장부호만 다른 복제도 잡는다
    assert not checker.leaks("요즘은 퇴근하고 동네 수영장 다니는 재미로 살아요")
    assert not checker.leaks("네")  # 짧은 관용 답은 누수로 보지 않는다

    result = SessionResult(profile=sample_profile(random.Random("l"), seed_id="s"))
    result.turns.append(
        TurnRecord(turnIndex=0, topicId="weekend", question="q", questionSource="seed", answer=gold, mode="normal")
    )
    assert checker.session_leaks(result)


def test_dry_run_writes_jsonl_and_summary(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")  # main() 이 끄는 값을 테스트 뒤에 되돌린다
    out = tmp_path / "sessions.jsonl"
    assert main(["--n", "3", "--dry-run", "--seed", "3", "--out", str(out)]) == 0
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3
    assert {r["familyId"] for r in rows} == {f"seed-3-{i:05d}" for i in range(3)}
    assert all(r["source"] == "synthetic" and r["history"] for r in rows)
    summary = json.loads(out.with_suffix(".summary.json").read_text(encoding="utf-8"))
    assert summary["written"] == 3 and summary["failed"] == 0

    # 같은 시드면 같은 데이터
    out2 = tmp_path / "again.jsonl"
    main(["--n", "3", "--dry-run", "--seed", "3", "--out", str(out2)])
    assert [r["seedProfile"] for r in rows] == [
        json.loads(line)["seedProfile"] for line in out2.read_text(encoding="utf-8").splitlines()
    ]


def test_v2_focus_section_by_level():
    from scripts.generate_synthetic_onboarding import DIMENSION_CUES, focus_section, unknown_style

    assert set(DIMENSION_CUES) == set(SCORED)
    profile = sample_profile(random.Random("f"), seed_id="seed-f")
    profile.levels |= {"avoidance": "high", "anxiety": "mid", "disclosure": "unanswered"}
    text = focus_section(profile, ["avoidance", "anxiety", "disclosure", "interests"])
    assert SCORED["avoidance"].high in text and DIMENSION_CUES["avoidance"]["high"][0] in text
    assert "상황에 따라 다름" in text
    assert unknown_style(profile, "disclosure") in text and "하나도 말하지 말고" in text
    assert unknown_style(profile, "disclosure") == unknown_style(profile, "disclosure")  # 세션·차원마다 고정
    for key in SCORED:
        assert key not in text


def test_v2_system_prompt_hides_dimension_levels():
    from scripts.generate_synthetic_onboarding import UserSimulator

    profile = sample_profile(random.Random("s"), seed_id="s")
    v2 = UserSimulator(base_url="http://localhost", api_key="", model="m").system_prompt(profile)
    v1 = UserSimulator(base_url="http://localhost", api_key="", model="m", prompt_version="v1").system_prompt(profile)
    assert all(SCORED[k].label not in v2 for k in SCORED)  # 성향은 턴마다 관련된 것만 준다
    assert any(SCORED[k].label in v1 for k in SCORED)


def test_session_passes_topic_dimensions_as_focus():
    seen = []

    class Recorder(FakeSimulator):
        async def answer(self, profile, history, question, mode, usage, focus=None):
            seen.append(focus)
            return await super().answer(profile, history, question, mode, usage, focus)

    rng = random.Random("r")
    profile = sample_profile(rng, seed_id="s")
    result = asyncio.run(run_session(profile, rng=rng, ask=seed_question, simulator=Recorder(), total_turns=3))
    for turn, focus in zip(result.turns, seen, strict=False):
        topic = TOPICS_BY_ID[turn.topicId]
        assert focus == [*topic.covers, *topic.also_touches]
