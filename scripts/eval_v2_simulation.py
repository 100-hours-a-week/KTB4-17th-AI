"""v2 시뮬레이션 하네스 평가 — 기획안 9절 지표를 실행 결과에서 계산한다.

사용법 (실제 LLM·DB 를 쓴다. 호출 예산 안에서만 돈다):
    uv run python scripts/eval_v2_simulation.py --pairs evals/sim_v2_pairs.json --repeats 3 --out evals/review/sim_v2.jsonl

pairs JSON: [{"me_user_id": "...", "partner_user_id": "..." | "partner_persona_id": "...", "kind": "hard" | "normal", "turns": 10}, ...]
쌍 구성은 docs/기획_및_설계/시뮬레이션_화자_혼동률_비교_측정_설계.md (어려운 쌍 8 + 일반 쌍 2) 를 따른다.
측정 전 해당 사용자들의 v2 메모리 백필(scripts/backfill_v2_persona_memory.py)을 먼저 해야 소재가 생긴다.

비용(Langfuse 집계)과 사람 표본 판정은 이 스크립트 밖에서 한다. 여기서는 규칙 기반 지표만 낸다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

MIXUP_TYPES = {"ownership", "self_address", "perspective"}


# ══ 지표 계산 (순수 함수, 테스트 대상) ═════════════════════════


def _pct(values: list[float], q: float) -> float | None:
    return round(float(np.percentile(np.asarray(values, dtype=np.float64), q)), 2) if values else None


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """runs: 실행 1건마다
    {kind, status, reason, elapsed_s, llm_calls, mode, final_issue_types: [..], line_count, echo_lines,
     early_farewell, effort_ratio, planned_topics, used_topic_refs}
    """
    out: dict[str, Any] = {"runs": len(runs)}
    if not runs:
        return out
    done = [r for r in runs if r["status"] == "succeeded"]
    out["failure_rate"] = round(1 - len(done) / len(runs), 4)

    for kind in ("hard", "normal"):
        subset = [r for r in runs if r.get("kind") == kind]
        if not subset:
            continue
        # 화자 혼동: 최종 대본에 혼동 규칙 위반이 남았거나, 혼동이 풀리지 않아 실패한 실행
        mixed = [
            r
            for r in subset
            if MIXUP_TYPES & set(r.get("final_issue_types", [])) or r.get("reason") == "speaker_mixup_unresolved"
        ]
        out[f"mixup_rate_{kind}"] = round(len(mixed) / len(subset), 4)

    lines = sum(r.get("line_count", 0) for r in done)
    out["echo_rate"] = round(sum(r.get("echo_lines", 0) for r in done) / lines, 4) if lines else None
    out["early_farewell_rate"] = (
        round(sum(bool(r.get("early_farewell")) for r in done) / len(done), 4) if done else None
    )
    ratios = [r["effort_ratio"] for r in done if r.get("effort_ratio") is not None]
    out["effort_ratio_median"] = round(statistics.median(ratios), 4) if ratios else None

    shared = [r for r in done if r.get("mode") == "shared"]
    if shared:
        # 계획된 소재 묶음(공통 소재 1개 = fact 2개) 중 대본에 하나라도 쓰인 묶음이 2개 이상인 실행
        def used_topics(r: dict[str, Any]) -> int:
            used = set(r.get("used_topic_refs", []))
            return sum(1 for group in r.get("planned_topics", []) if used & set(group))

        used_two = [r for r in shared if used_topics(r) >= 2]
        out["topic_use_rate"] = round(len(used_two) / len(shared), 4)

    calls = [r["llm_calls"] for r in runs if r.get("llm_calls") is not None]
    out["llm_calls_mean"] = round(statistics.mean(calls), 2) if calls else None
    out["llm_calls_max"] = max(calls) if calls else None
    elapsed = [r["elapsed_s"] for r in runs if r.get("elapsed_s") is not None]
    out["elapsed_p50"] = _pct(elapsed, 50)
    out["elapsed_p95"] = _pct(elapsed, 95)
    return out


def effort_ratio(texts: list[str]) -> float | None:
    """후반 30% 평균 길이 / 전반 70% 평균 길이."""
    n = len(texts)
    cut = int(round(n * 0.7))
    if not 0 < cut < n:
        return None
    front = sum(map(len, texts[:cut])) / cut
    back = sum(map(len, texts[cut:])) / (n - cut)
    return round(back / front, 4) if front else None


def _planned_topics(trace: list[dict[str, Any]]) -> list[list[int]]:
    """plan_topics note 의 "topics=1+11|2" → [[1, 11], [2]]."""
    note = next((e.get("note") or "" for e in trace if e.get("node") == "plan_topics"), "")
    raw = next((part[len("topics=") :] for part in note.split() if part.startswith("topics=")), "")
    return [[int(x) for x in g.split("+") if x.isdigit()] for g in raw.split("|") if g]


# ══ 실행 (실제 LLM) ═══════════════════════════════════════════


async def _run_one(pair: dict[str, Any]) -> dict[str, Any]:
    from app.core.db import SessionLocal
    from app.features.simulation.schemas import SimulationRequest
    from app.v2.simulation.models import V2Simulation
    from app.v2.simulation.service import SimulationV2Service

    req = SimulationRequest(
        me_user_id=pair["me_user_id"],
        partner_user_id=pair.get("partner_user_id"),
        partner_persona_id=pair.get("partner_persona_id"),
        turns=pair.get("turns", 10),
    )
    started = time.monotonic()
    async with SessionLocal() as db:
        svc = SimulationV2Service(db)
        job = await svc.start(req)
        await db.commit()
        job_id = job.id
    async with SessionLocal() as db:
        await SimulationV2Service(db).run(job_id)
    elapsed = time.monotonic() - started

    async with SessionLocal() as db:
        job = await SimulationV2Service(db).get_job(job_id)
        row: dict[str, Any] = {
            "kind": pair.get("kind", "normal"),
            "job_id": job_id,
            "status": job.status,
            "reason": job.error,
            "elapsed_s": round(elapsed, 2),
        }
        if job.status != "succeeded" or not job.simulation_id:
            return row
        sim = await db.get(V2Simulation, job.simulation_id)
        trace = sim.harness_trace or []
        last_rule = next((e for e in reversed(trace) if e.get("node") == "rule_check"), {})
        final_types = [x["type"] for x in last_rule.get("issues", [])]
        texts = [t["text"] for t in sim.transcript]
        llm_nodes = {"generate", "regenerate", "repair", "judge", "write_report"}
        row.update(
            simulation_id=sim.id,
            mode=sim.mode,
            llm_calls=sum(
                1
                for e in trace
                if e.get("node") in llm_nodes and e.get("note") not in {"judge_unaffordable", "template_report"}
            ),
            final_issue_types=final_types + list(sim.quality_warnings or []),
            line_count=len(texts),
            echo_lines=final_types.count("echo"),  # 경고(quality_warnings)에도 같은 이슈가 있으니 한쪽만 센다
            early_farewell="early_farewell" in final_types or "early_farewell" in (sim.quality_warnings or []),
            effort_ratio=effort_ratio(texts),
            planned_topics=_planned_topics(trace),
            used_topic_refs=[t["fact_id"] for t in sim.topics_used or []],
        )
        return row


def _append(out: Path, row: dict[str, Any]) -> None:
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


async def _main(pairs: list[dict[str, Any]], repeats: int, out: Path) -> int:
    runs = []
    for rep in range(repeats):
        for pair in pairs:
            try:
                row = await _run_one(pair)
            except Exception as e:  # 한 쌍의 실패로 전체 측정을 멈추지 않는다
                row = {"kind": pair.get("kind", "normal"), "status": "error", "reason": type(e).__name__}
            row["repeat"] = rep
            runs.append(row)
            await asyncio.to_thread(_append, out, row)
            print(json.dumps(row, ensure_ascii=False), file=sys.stderr)
    print(json.dumps(summarize(runs), ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--pairs", required=True)
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--out", default="evals/review/sim_v2.jsonl")
    args = p.parse_args()
    pairs = json.loads(Path(args.pairs).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    return asyncio.run(_main(pairs, args.repeats, out))


if __name__ == "__main__":
    raise SystemExit(main())
