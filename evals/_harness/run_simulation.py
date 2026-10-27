"""기능 10~16 샘플 — 시뮬레이션 v1(POST /v1/simulation) 30회, v2(POST /v2/simulation) 30회.

짝: 사용자 i(나) × 사용자 (i+7)%30(상대). 앞 단계에서 확정 페르소나·카톡 말투·메모리가 쌓인 사용자들이다.
v1 turns: 3 ×20, 5 ×10 / v2 turns: 3·4·5·6 순환.
v2 단계별 결과(생성·심사·수정·리포트)는 DB v2_simulations.harness_trace 와 Langfuse 원문으로 나눈다 (attach_raw.py).
"""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from common import EVALS, body, client, read_jsonl, timed, write_jsonl


def psql(sql: str) -> str:
    return subprocess.run(
        ["psql", "-d", "ktb_eval", "-tAc", sql], capture_output=True, text=True, check=True
    ).stdout.strip()


def run(args) -> dict:
    ver, k, me, partner, turns = args
    c = client(timeout=300)
    r, ms, err = timed(
        c.post,
        f"/{ver}/simulation",
        json={"me_user_id": me["user_id"], "partner_user_id": partner["user_id"], "turns": turns},
    )
    b = body(r) if r is not None else None
    out = {
        "ver": ver,
        "k": k,
        "me": me,
        "partner": partner,
        "turns": turns,
        "latency_ms": ms,
        "error": err,
        "response": b,
    }
    if ver == "v2":
        sim_id = (b or {}).get("simulation_id") if not err else None
        job_id = (
            ((b or {}).get("detail") or {}).get("job_id") if err and isinstance((b or {}).get("detail"), dict) else None
        )
        if sim_id:
            row = psql(
                f"select json_build_object('harness_trace', harness_trace, 'quality_warnings', quality_warnings, "
                f"'mode', mode) from v2_simulations where id='{sim_id}'"
            )
            out["db"] = json.loads(row) if row else None
        elif job_id:
            out["db"] = {"job_error": psql(f"select coalesce(error,'') from v2_simulation_jobs where id='{job_id}'")}
    return out


def main() -> None:
    which = sys.argv[1:] or ["v1", "v2"]
    logs = read_jsonl(EVALS / "_harness/runs/onboarding_logs.jsonl")
    users = [
        {
            "user_id": lg["user_id"],
            "nickname": lg["nickname"],
            "case_id": lg["case"]["id"],
            "case_type": lg["case"]["case_type"],
        }
        for lg in logs
    ]
    for ver in which:
        turns = ([3] * 20 + [5] * 10) if ver == "v1" else [3, 4, 5, 6] * 8
        jobs = [(ver, k, users[k], users[(k + 7) % 30], turns[k]) for k in range(30)]
        with ThreadPoolExecutor(max_workers=4) as ex:
            res = list(ex.map(run, jobs))
        write_jsonl(EVALS / f"_harness/runs/simulation_{ver}_logs.jsonl", res)
        print(
            ver,
            "runs",
            len(res),
            "errors",
            sum(1 for x in res if x["error"]),
            [str(x["error"])[:120] for x in res if x["error"]][:3],
        )


if __name__ == "__main__":
    main()
