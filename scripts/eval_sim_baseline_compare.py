"""시뮬레이션 기준선 비교 (기획안 M0) — v1-old · v1-graph · v2 를 같은 쌍으로 실행해 지연·성공·화자 혼동·비용을 잰다.

사용법 (실행 중인 서버, 실제 LLM):
    uv run python scripts/eval_sim_baseline_compare.py --me u-minsu --partner u-jieun --runs 8 --out results.json

- 화자 혼동은 세 방식 모두 같은 잣대(v2 verifier.rule_check, 소재 계획 없이)로 최종 대본을 검사한다.
  v2 는 혼동이 남으면 job 을 실패시키므로 "실패"도 함께 본다 — 사용자에게 혼동 대본이 나갔는지가 핵심이다.
- 비용은 OpenRouter 키 사용량(/api/v1/key 의 usage, 달러) 배치 전후 차이 ÷ 실행 수. 같은 키를 다른 곳에서 동시에 쓰면 섞인다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

import httpx

BASE = "http://127.0.0.1:8765/ai/api"
H = {"X-Internal-Api-Key": "local-test"}
MIXUP = {"self_address", "perspective", "ownership", "fabrication"}


def key_usage(api_key: str) -> float:
    r = httpx.get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {api_key}"}, timeout=20)
    return float(r.json()["data"]["usage"])


def check_transcript(turns: list[dict[str, Any]], me: str, partner: str) -> dict[str, Any]:
    """최종 대본을 v2 규칙으로 검사한다 (소재 계획 없음 → 소유권 검사는 빠진다)."""
    from app.features.persona.schemas import PersonaResponse
    from app.v2.simulation.schemas import ScriptLineV2, SimContext, TopicPlan
    from app.v2.simulation.verifier import rule_check

    script = [ScriptLineV2(i=k, speaker=t["speaker"], text=t["text"]) for k, t in enumerate(turns)]
    ctx = SimContext(
        me_user_id="x",
        partner_user_id="y",
        persona_a_id="a",
        persona_b_id="b",
        persona_a=_PERSONAS["a"] or PersonaResponse(persona_id="a", scores={}),
        persona_b=_PERSONAS["b"] or PersonaResponse(persona_id="b", scores={}),
        name_a=me,
        name_b=partner,
    )
    issues, warnings = rule_check(script, ctx, TopicPlan(mode="persona_only"), max(len(script) // 2, 1))
    types = [x.type for x in issues if x.type != "format"]
    return {
        "mixup_lines": sorted({x.i for x in issues if x.type in MIXUP and x.i is not None}),
        "issue_types": types,
        "warnings": warnings,
        "lines": len(script),
    }


_PERSONAS: dict[str, Any] = {"a": None, "b": None}


def run_v1(path: str, body: dict, c: httpx.Client) -> tuple[bool, list[dict], str | None]:
    r = c.post(path, json=body)
    if r.status_code != 201:
        return False, [], f"{r.status_code}:{str(r.json())[:80]}"
    return True, r.json()["transcript"], None


def run_v2(body: dict, c: httpx.Client) -> tuple[bool, list[dict], str | None]:
    r = c.post("/v2/simulation", json=body)
    if r.status_code != 202:
        return False, [], f"{r.status_code}"
    job_id = r.json()["job_id"]
    while True:
        j = c.get(f"/v2/simulation/jobs/{job_id}").json()
        if j["status"] in ("succeeded", "failed"):
            break
        time.sleep(1)
    if j["status"] != "succeeded":
        return False, [], j["reason"]
    res = c.get(f"/v2/simulation/{j['simulation_id']}").json()
    return True, res["transcript"], None


async def _load_personas(me: str, partner: str) -> None:
    from app.core.db import SessionLocal, engine
    from app.features.persona.lookup import load_persona
    from app.features.persona.schemas import PersonaRef

    async with SessionLocal() as db:
        a = await load_persona(db, PersonaRef(user_id=me))
        b = await load_persona(db, PersonaRef(user_id=partner))
        _PERSONAS["a"] = a.response if a else None
        _PERSONAS["b"] = b.response if b else None
        names = (a.nickname if a else me, b.nickname if b else partner)
    await engine.dispose()
    return names


def main() -> int:
    from app.core.config import get_settings

    p = argparse.ArgumentParser()
    p.add_argument("--me", required=True)
    p.add_argument("--partner", required=True)
    p.add_argument("--runs", type=int, default=8)
    p.add_argument("--out", required=True)
    p.add_argument("--only", default="v1_old,v1_graph,v2")
    a = p.parse_args()
    name_me, name_partner = asyncio.run(_load_personas(a.me, a.partner))
    api_key = get_settings().llm_api_key
    body = {"me_user_id": a.me, "partner_user_id": a.partner, "turns": 10}
    c = httpx.Client(base_url=BASE, headers=H, timeout=300)
    runners = {
        "v1_old": lambda: run_v1("/v1/simulation_old", body, c),
        "v1_graph": lambda: run_v1("/v1/simulation", body, c),
        "v2": lambda: run_v2(body, c),
    }
    out: dict[str, Any] = {}
    for name in a.only.split(","):
        before = key_usage(api_key)
        rows = []
        for k in range(a.runs):
            t = time.monotonic()
            try:
                ok, turns, err = runners[name]()
            except Exception as e:  # 한 번의 실패로 측정을 멈추지 않는다
                ok, turns, err = False, [], type(e).__name__
            row = {"run": k + 1, "ok": ok, "error": err, "wall_s": round(time.monotonic() - t, 1)}
            if ok:
                row.update(check_transcript(turns, name_me, name_partner))
                row["transcript"] = turns
            rows.append(row)
            print(name, k + 1, ok, err, row["wall_s"], row.get("mixup_lines"), flush=True)
        time.sleep(5)  # 사용량 집계 반영 대기
        spent = key_usage(api_key) - before
        delivered = [r for r in rows if r["ok"]]
        walls = [r["wall_s"] for r in rows]
        out[name] = {
            "runs": len(rows),
            "delivered": len(delivered),
            "delivered_with_mixup": sum(1 for r in delivered if r["mixup_lines"]),
            "failed": len(rows) - len(delivered),
            "wall_p50": statistics.median(walls),
            "wall_max": max(walls),
            "cost_usd_total": round(spent, 6),
            "cost_usd_per_run": round(spent / len(rows), 6),
            "rows": rows,
        }
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        summary = {k: v for k, v in out[name].items() if k != "rows"}
        print(name, json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
