"""teacher 라벨링 — 가짜 teacher 로 집계·기록·이어서 실행 규칙을 확인한다. 네트워크 없음."""

import asyncio
import itertools
import json
import random

from app.features.persona.agents import RUBRIC, TAG_PROMPT
from app.features.persona.service import ONBOARDING_TOTAL_TURNS
from scripts.generate_synthetic_onboarding import (
    FakeSimulator,
    run_session,
    sample_profile,
    seed_question,
    to_record,
)
from scripts.label_with_teacher import (
    GOLD_DIR,
    Completion,
    aggregate_tags,
    check_tag_sample,
    gold_items,
    parse_json,
    run,
    summarize,
    synthetic_items,
)


def _synthetic_file(tmp_path, off_topic_rate=0.0):
    rng = random.Random("teacher")
    profile = sample_profile(rng, seed_id="seed-x")
    profile.style.off_topic_rate = off_topic_rate
    profile.style.joke_rate = profile.style.short_rate = 0.0
    result = asyncio.run(
        run_session(profile, rng=rng, ask=seed_question, simulator=FakeSimulator(), total_turns=ONBOARDING_TOTAL_TURNS)
    )
    path = tmp_path / "sessions.jsonl"
    path.write_text(json.dumps(to_record(result, index=0, run_seed=0, models={}), ensure_ascii=False) + "\n")
    return path


class FakeTeacher:
    """태깅은 순서대로 정해 둔 답을, 추출은 고정 JSON 을 돌려준다. 받은 입력을 기록한다."""

    def __init__(self, tag_outputs):
        self.tag_outputs = itertools.cycle(tag_outputs)
        self.calls = []

    async def __call__(self, system, user, temperature, max_tokens):
        self.calls.append((system, user, temperature))
        if system == TAG_PROMPT:
            return Completion(next(self.tag_outputs), 10, 5)
        assert system == RUBRIC
        return Completion('{"avoidance": 70, "anxiety": 40, "interests": ["러닝"]}', 100, 50)


def test_parse_json_strips_reasoning_and_fences():
    assert parse_json('<think>음... {"x": 1}</think>```json\n{"primary": []}\n```') == {"primary": []}


def test_check_tag_sample_records_violations_instead_of_hiding_them():
    s = check_tag_sample({"primary": ["avoidance", "mbti"], "secondary": [], "off_topic": False, "score": 80})
    assert s["primary"] == ["avoidance"]
    assert s["invalidDimensions"] == ["mbti"]
    assert s["extraKeys"] == ["score"]


def test_aggregate_majority_soft_labels_and_agreement():
    def s(primary, off=False):
        return {"primary": primary, "secondary": [], "off_topic": off}

    agg = aggregate_tags([s(["avoidance"]), s(["avoidance"]), s(["avoidance", "anxiety"]), s([]), {"error": "parse"}])
    assert agg["validSamples"] == 4
    assert agg["primaryVotes"] == {"anxiety": 0.25, "avoidance": 0.75}
    assert agg["majority"]["primary"] == ["avoidance"]  # 4개 중 3개 이상
    assert agg["agreement"] == 0.5

    off = aggregate_tags([s([], True), s([], True), s(["routine"]), s([], True), s([], True)])
    assert off["majority"] == {"primary": [], "secondary": [], "off_topic": True}


def test_gold_items_skip_holdout_and_code_behavior_by_default():
    tags, extracts = gold_items(GOLD_DIR, include_holdout=False)
    assert tags and extracts
    assert all(t.split != "blind_holdout" for t in tags + extracts)
    assert len(tags) == 63  # label_accuracy 79건 중 holdout 16건 제외
    assert len(extracts) == 24  # label_accuracy 30건 중 holdout 6건 제외
    all_tags, _ = gold_items(GOLD_DIR, include_holdout=True)
    assert len(all_tags) == 79


def test_synthetic_items_include_off_topic_answers(tmp_path):
    tags, extracts = synthetic_items(_synthetic_file(tmp_path, off_topic_rate=1.0))
    off = [t for t in tags if t.context.get("intendedOffTopic")]
    assert off and all(t.id.endswith(":offtopic") for t in off)
    assert len(extracts) == 1 and extracts[0].answered is None
    assert all(":offtopic" not in tid for tid in extracts[0].tag_ids)  # answered 계산에서 뺀다


def test_run_uses_production_prompts_and_majority_answered(tmp_path):
    tags, extracts = synthetic_items(_synthetic_file(tmp_path))
    teacher = FakeTeacher(['{"primary": ["avoidance"], "secondary": [], "off_topic": false}'])
    out = tmp_path / "out"
    out.mkdir()
    asyncio.run(
        run(
            tags,
            extracts,
            teacher,
            out_dir=out,
            samples=5,
            temperature=0.7,
            concurrency=4,
            resume=False,
            teacher_meta={},
        )
    )
    tag_calls = [c for c in teacher.calls if c[0] == TAG_PROMPT]
    assert len(tag_calls) == 5 * len(tags)
    assert tag_calls[0][1].startswith("질문: ") and "\n답변: " in tag_calls[0][1]  # 운영 입력 형식
    extract_call = next(c for c in teacher.calls if c[0] == RUBRIC)
    assert extract_call[2] == 0.0
    assert "# 사용자가 직접 답한 항목" in extract_call[1]

    row = json.loads((out / "extraction.jsonl").read_text().splitlines()[0])
    assert row["input"]["answered"] == ["avoidance"]
    assert row["unansweredKeys"] == ["anxiety", "interests"]  # 허용 밖 키를 기록
    assert row["teacher"]["tagPromptVersion"] and row["teacher"]["extractPromptVersion"]

    q = summarize(out)
    assert q["tagging"]["meanAgreement"] == 1.0
    assert q["extraction"]["itemsWithUnansweredKeys"] == 1


def test_resume_skips_done_items_and_retries_failures(tmp_path):
    tags, extracts = synthetic_items(_synthetic_file(tmp_path))
    out = tmp_path / "out"
    out.mkdir()
    ok = FakeTeacher(['{"primary": [], "secondary": [], "off_topic": false}'])
    asyncio.run(
        run(tags[:3], [], ok, out_dir=out, samples=2, temperature=0.7, concurrency=2, resume=False, teacher_meta={})
    )

    again = FakeTeacher(['{"primary": [], "secondary": [], "off_topic": false}'])
    asyncio.run(
        run(tags, [], again, out_dir=out, samples=2, temperature=0.7, concurrency=2, resume=True, teacher_meta={})
    )
    assert len([c for c in again.calls if c[0] == TAG_PROMPT]) == 2 * (len(tags) - 3)
    ids = [json.loads(x)["id"] for x in (out / "tagging.jsonl").read_text().splitlines()]
    assert sorted(ids) == sorted(t.id for t in tags)
