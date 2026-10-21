"""합성 세션 질문 다시 쓰기 — LLM 없이 가짜 질문 함수로 흐름 규칙만 확인한다."""

import asyncio
import json
import random

from app.features.persona.service import ONBOARDING_TOTAL_TURNS
from scripts.generate_synthetic_onboarding import (
    FakeSimulator,
    run_session,
    sample_profile,
    seed_question,
    to_record,
)
from scripts.rewrite_synthetic_questions import main, rewrite_session


def _record(seed: str = "t", **style) -> dict:
    rng = random.Random(seed)
    profile = sample_profile(rng, seed_id="seed-test")
    for key, value in style.items():
        setattr(profile.style, key, value)
    result = asyncio.run(
        run_session(profile, rng=rng, ask=seed_question, simulator=FakeSimulator(), total_turns=ONBOARDING_TOTAL_TURNS)
    )
    return to_record(result, index=0, run_seed=0, models={"conversationModel": "seed"})


def _recording_ask(calls: list[dict]):
    async def ask(*, history, topic, turn_index, total_turns, nickname):
        calls.append({"history": list(history), "topic": topic.id, "turn_index": turn_index, "nickname": nickname})
        return f"새 질문 {turn_index}", "llm"

    return ask


def test_rewrite_keeps_answers_and_records_previous_question():
    record = _record(off_topic_rate=0.0)
    calls: list[dict] = []
    new, usage = asyncio.run(rewrite_session(record, ask=_recording_ask(calls), total_turns=ONBOARDING_TOTAL_TURNS))

    assert [t["answer"] for t in new["turns"]] == [t["answer"] for t in record["turns"]]
    assert [t["topicId"] for t in new["turns"]] == [t["topicId"] for t in record["turns"]]
    assert [t["previousQuestion"] for t in new["turns"]] == [t["question"] for t in record["turns"]]
    assert all(t["question"].startswith("새 질문") and t["questionSource"] == "llm" for t in new["turns"])
    assert usage.conversation_calls == len(record["turns"])
    # 원본은 그대로
    assert all(not t["question"].startswith("새 질문") for t in record["turns"])


def test_rewrite_history_uses_new_questions_and_original_answers():
    record = _record(off_topic_rate=0.0)
    calls: list[dict] = []
    new, _ = asyncio.run(rewrite_session(record, ask=_recording_ask(calls), total_turns=ONBOARDING_TOTAL_TURNS))

    assert calls[0]["history"] == []
    assert calls[1]["history"] == [
        {"role": "assistant", "content": "새 질문 0"},
        {"role": "user", "content": record["turns"][0]["answer"]},
    ]
    assert all(c["nickname"] == record["seedProfile"]["nickname"] for c in calls)
    assert new["history"][-2:] == [
        {"role": "assistant", "content": f"새 질문 {len(calls) - 1}"},
        {"role": "user", "content": record["turns"][-1]["answer"]},
    ]


def test_rewrite_keeps_reask_from_original_session():
    record = _record(off_topic_rate=1.0)
    reasks = [t["reask"] for t in record["turns"]]
    assert any(reasks)
    new, _ = asyncio.run(rewrite_session(record, ask=_recording_ask([]), total_turns=ONBOARDING_TOTAL_TURNS))
    assert [t["reask"] for t in new["turns"]] == reasks


def test_main_dry_run_writes_outputs_and_refuses_overwrite(tmp_path):
    src = tmp_path / "sessions.jsonl"
    src.write_text("".join(json.dumps(_record(s), ensure_ascii=False) + "\n" for s in ("a", "b")), encoding="utf-8")

    assert main([str(src), "--dry-run"]) == 0
    out = tmp_path / "sessions.rewritten.jsonl"
    records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    summary = json.loads(out.with_suffix(".summary.json").read_text(encoding="utf-8"))
    assert len(records) == 2
    assert records[0]["generator"]["questionRewrite"]["conversationModel"] == "seed"
    assert summary["written"] == 2 and summary["failed"] == 0
    assert summary["seedFallbackTurns"] == summary["turns"]

    assert main([str(src), "--out", str(src)]) == 2


def test_unknown_topic_fails_only_that_session(tmp_path):
    good, bad = _record("a"), _record("b")
    bad["turns"][0]["topicId"] = "no-such-topic"
    src = tmp_path / "sessions.jsonl"
    src.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in (good, bad)), encoding="utf-8")

    assert main([str(src), "--dry-run"]) == 0
    summary = json.loads((tmp_path / "sessions.rewritten.summary.json").read_text(encoding="utf-8"))
    assert summary["written"] == 1 and summary["failed"] == 1
