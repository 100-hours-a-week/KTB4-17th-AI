"""합성 온보딩 세션의 하루 질문 다시 쓰기 — 답변은 두고 질문만 지금 운영 ConversationAgent 로 다시 만든다.

generate_synthetic_onboarding.py 로 만든 세션은 만들 당시의 ConversationAgent 프롬프트로 질문이 생성됐다.
그 뒤 온보딩 발화 프롬프트가 바뀌면(예: #168 문맥·근거, #181 세 박자 틀) 학습 입력의 질문 분포가 운영과 달라진다.
시뮬레이터 답변(비용의 대부분)은 그대로 두고, 질문만 운영 모델(LLM_MODEL)로 다시 쓴다.

  - 턴마다 history = 이미 다시 쓴 질문 + 원래 답변. 운영에서 하루가 보는 대화와 같은 모양이다.
  - 주제(topicId)·답변 방식·되묻기는 원래 세션을 따른다. 되묻기 질문은 운영처럼 LLM 없이 시드라 바꾸지 않는다.
  - 원래 질문은 turns[].previousQuestion 에 남긴다. 새 질문과 답이 어긋나는지 검수할 때 비교한다.
  - LLM 이 실패해 시드 질문으로 떨어진 턴은 questionSource="seed" 로 남고 요약에 따로 센다.

    .venv/bin/python scripts/rewrite_synthetic_questions.py data/synthetic/sessions.jsonl \\
        --out data/synthetic/sessions.rewritten.jsonl --price-in 0.1 --price-out 0.4
    .venv/bin/python scripts/rewrite_synthetic_questions.py data/synthetic/sessions.jsonl --limit 3 --dry-run

Langfuse 트레이스는 운영 지표를 오염시키지 않도록 기본으로 끈다 (--trace 로 켠다).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.generate_synthetic_onboarding import (  # noqa: E402
    QuestionFn,
    Usage,
    estimate_cost,
    make_question_fn,
    seed_question,
)

REWRITER_VERSION = "rewrite-questions/1"


async def rewrite_session(record: dict, *, ask: QuestionFn, total_turns: int) -> tuple[dict, Usage]:
    """세션 하나의 질문을 차례로 다시 쓴다. 원본 record 는 바꾸지 않는다."""
    from app.features.persona.schemas import TOPICS_BY_ID

    usage = Usage()
    nickname = record["seedProfile"]["nickname"]
    history: list[dict] = []
    turns: list[dict] = []
    for turn in record["turns"]:
        topic = TOPICS_BY_ID.get(turn["topicId"])
        if topic is None:
            raise ValueError(f"알 수 없는 topicId: {turn['topicId']}")
        question, source = await ask(
            history=history,
            topic=topic,
            turn_index=turn["turnIndex"],
            total_turns=total_turns,
            nickname=nickname,
        )
        if source == "llm":
            usage.conversation_calls += 1
        turns.append(
            {
                **turn,
                "question": question,
                "questionSource": source,
                "previousQuestion": turn["question"],
                "previousQuestionSource": turn.get("questionSource"),
            }
        )
        history += [{"role": "assistant", "content": question}, {"role": "user", "content": turn["answer"]}]
    return {**record, "turns": turns, "history": history}, usage


def load_sessions(path: Path, limit: int | None) -> list[dict]:
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return records[:limit] if limit else records


async def rewrite(args: argparse.Namespace) -> tuple[list[dict], dict]:
    from app.core.config import get_settings
    from app.features.persona.service import ONBOARDING_TOTAL_TURNS

    total_turns = args.total_turns or ONBOARDING_TOTAL_TURNS
    if args.dry_run:
        ask: QuestionFn = seed_question
        model = "seed"
    else:
        from app.features.persona.agents import ConversationAgent

        ask = make_question_fn(ConversationAgent())
        model = get_settings().llm_model

    sessions = load_sessions(Path(args.sessions), args.limit)
    meta = {
        "version": REWRITER_VERSION,
        "conversationModel": model,
        "rewrittenAt": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    sem = asyncio.Semaphore(args.concurrency)
    results: dict[int, dict] = {}
    failed: list[dict] = []
    total_usage = Usage()

    async def one(index: int, record: dict) -> None:
        async with sem:
            try:
                new, usage = await rewrite_session(record, ask=ask, total_turns=total_turns)
            except Exception as e:  # 한 세션 실패로 전체를 버리지 않는다
                failed.append({"sessionId": record.get("sessionId"), "error": f"{type(e).__name__}: {e}"})
                return
        total_usage.add(usage)
        new["generator"] = {**record.get("generator", {}), "questionRewrite": meta}
        results[index] = new
        print(f"  [{len(results) + len(failed)}/{len(sessions)}] {record.get('sessionId')} ok", file=sys.stderr)

    started = time.monotonic()
    await asyncio.gather(*(one(i, r) for i, r in enumerate(sessions)))
    elapsed = time.monotonic() - started

    records = [results[i] for i in sorted(results)]
    turns = [t for r in records for t in r["turns"]]
    summary = {
        **meta,
        "input": str(args.sessions),
        "out": str(args.out),
        "requested": len(sessions),
        "written": len(records),
        "failed": len(failed),
        "turns": len(turns),
        "seedFallbackTurns": sum(t["questionSource"] == "seed" for t in turns),
        "unchangedTurns": sum(t["question"] == t["previousQuestion"] for t in turns),
        "elapsedSeconds": round(elapsed, 1),
        "usage": asdict(total_usage),
        "cost": estimate_cost(total_usage, args.price_in, args.price_out),
        "failures": failed,
    }
    return records, summary


def write_outputs(out: Path, records: list[dict], summary: dict) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    out.with_suffix(".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("sessions", type=Path, help="generate_synthetic_onboarding.py 출력 JSONL")
    parser.add_argument(
        "--out", type=Path, default=None, help="기본은 입력 옆 <이름>.rewritten.jsonl (옆에 .summary.json)"
    )
    parser.add_argument("--limit", type=int, default=None, help="앞에서부터 N세션만")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--total-turns", type=int, default=None, help="기본은 운영과 같은 ONBOARDING_TOTAL_TURNS")
    parser.add_argument("--dry-run", action="store_true", help="LLM 없이 시드 질문으로 흐름만 확인")
    parser.add_argument("--trace", action="store_true", help="Langfuse 트레이스 켜기 (기본 끔)")
    parser.add_argument("--price-in", type=float, default=None, help="입력 100만 토큰당 USD (비용 추정용)")
    parser.add_argument("--price-out", type=float, default=None, help="출력 100만 토큰당 USD (비용 추정용)")
    args = parser.parse_args(argv)
    if args.out is None:
        args.out = args.sessions.with_name(f"{args.sessions.stem}.rewritten.jsonl")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.out.resolve() == args.sessions.resolve():
        print("--out 이 입력과 같으면 원본을 덮어쓴다. 다른 경로를 주세요.", file=sys.stderr)
        return 2
    if not args.trace:
        # 다시 쓴 질문 호출이 운영 Langfuse 지표에 섞이지 않게 한다. app import 전에 정해야 한다
        os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    records, summary = asyncio.run(rewrite(args))
    write_outputs(args.out, records, summary)
    print(
        f"written={summary['written']} failed={summary['failed']} "
        f"seedFallback={summary['seedFallbackTurns']}/{summary['turns']} -> {args.out}",
        file=sys.stderr,
    )
    return 0 if records else 1


if __name__ == "__main__":
    raise SystemExit(main())
