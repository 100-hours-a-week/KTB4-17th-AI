"""v2 시뮬레이션 반복 실행 — 실행 중인 서버에 시뮬레이션을 N 회 요청하고 상태 단계·결과를 JSON 으로 남긴다.

사용법: uv run python scripts/v2_live_sim_repeat.py <결과.json> [N]
"""

import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8765/ai/api"
c = httpx.Client(base_url=BASE, headers={"X-Internal-Api-Key": "local-test"}, timeout=60)
OUT = Path(sys.argv[1])
N = int(sys.argv[2]) if len(sys.argv) > 2 else 6
steps = []


def save():
    OUT.write_text(json.dumps(steps, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


for k in range(N):
    req = {"me_user_id": "u-minsu", "partner_user_id": "u-jieun", "turns": 10}
    t0 = time.monotonic()
    r = c.post("/v2/simulation", json=req)
    step = {"run": k + 1, "request": req, "post_status": r.status_code, "post_response": r.json()}
    if r.status_code != 202:
        steps.append(step)
        save()
        continue
    job_id = r.json()["job_id"]
    if k == 0:
        dup_req = {"me_user_id": "u-minsu", "partner_persona_id": "persona-jieun"}  # 옛 페르소나 버전으로 같은 상대
        d = c.post("/v2/simulation", json=dup_req)
        step["duplicate"] = {"request": dup_req, "status": d.status_code, "response": d.json()}
    stages = []
    while True:
        j = c.get(f"/v2/simulation/jobs/{job_id}")
        b = j.json()
        if not stages or stages[-1] != [b["status"], b["stage"]]:
            stages.append([b["status"], b["stage"]])
        if b["status"] in ("succeeded", "failed"):
            break
        step.setdefault("retry_after", j.headers.get("retry-after"))
        time.sleep(1)
    step.update(job=b, stages=stages, wall_s=round(time.monotonic() - t0, 1))
    if b.get("simulation_id"):
        step["result"] = c.get(f"/v2/simulation/{b['simulation_id']}").json()
    steps.append(step)
    save()
    print(k + 1, b["status"], b["reason"], step["wall_s"], flush=True)
print("done")
