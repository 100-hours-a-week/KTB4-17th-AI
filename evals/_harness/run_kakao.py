"""기능 4·5 샘플 — 합성 카톡 대화를 v1·v2 추출 엔드포인트에 올리고 작업 결과를 받는다.

케이스 i 는 온보딩에서 확정한 사용자 i 의 페르소나에 반영된다.
짝수 케이스는 v1 → v2, 홀수 케이스는 v2 → v1 순서 — 각 기능의 절반은 '이전 스타일 없음', 절반은 '있음'.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from common import EVALS, body, client, read_jsonl, timed, write_jsonl

POLL_S = 2.0
MAX_WAIT_S = 120


def persona_now(c, session_id: str) -> dict | None:
    r, _, err = timed(c.get, f"/v1/persona/{session_id}")
    return None if err else body(r)


def run_one(c, ver: str, case: dict, user: dict) -> dict:
    before = persona_now(c, user["session_id"])
    files = {
        "user_id": (None, user["user_id"]),
        "speaker_name": (None, case["speaker_name"]),
        "text": (None, case["text"]),
    }
    started = time.perf_counter()
    r, ms, err = timed(c.post, f"/{ver}/persona-extraction/conversation", files=files)
    job, final = body(r) if r is not None else None, None
    if not err and job and job.get("job_id"):
        deadline = time.time() + MAX_WAIT_S
        while time.time() < deadline:
            jr, _, jerr = timed(c.get, f"/{ver}/persona-extraction/jobs/{job['job_id']}")
            final = body(jr) if jr is not None else None
            if jerr or (final or {}).get("status") in ("succeeded", "failed"):
                break
            time.sleep(POLL_S)
    total_ms = int((time.perf_counter() - started) * 1000)
    after = persona_now(c, user["session_id"])
    return {"job": final or job, "submit_error": err, "latency_ms": total_ms, "before": before, "after": after}


def run_case(args) -> dict:
    case, user, order = args
    c = client()
    out = {"case": case, "user": user, "order": order}
    for ver in order:
        out[ver] = run_one(c, ver, case, user)
    return out


def sample(slug_ver: str, res: dict) -> dict:
    case, user, r = res["case"], res["user"], res[slug_ver]
    job = r.get("job") or {}
    had_prev = res["order"].index(slug_ver) == 1
    after = r.get("after") or {}
    err = r.get("submit_error") or (job.get("error") if job.get("status") == "failed" else None)
    return {
        "id": f"{case['id']}-{slug_ver}",
        "case_id": case["id"],
        "case_type": case["case_type"],
        "label": case["label"],
        "source": case["source"],
        "splitGroup": case["splitGroup"],
        "user_id": user["user_id"],
        "job_id": job.get("job_id"),
        "input": {
            "speaker_name": case["speaker_name"],
            "format": case["format"],
            "my_lines": case["my_lines"],
            "had_previous_style": had_prev,
            "text": case["text"],
        },
        "output_raw": None,
        "output_parsed": {
            "job_status": job.get("status"),
            "analyzed_count": job.get("analyzed_count"),
            "conversation_style": after.get("conversation_style"),
            "scores_after": after.get("scores"),
            "scores_before": (r.get("before") or {}).get("scores"),
            "confidence_after": after.get("confidence"),
            "narrative_kept": bool(after.get("narrative")),
        },
        "latency_ms": r.get("latency_ms"),
        "model": None,
        "fallback_used": None,
        "error": err,
    }


def main() -> None:
    cases = read_jsonl(EVALS / "f04-persona-extraction-style/inputs.jsonl")
    logs = read_jsonl(EVALS / "_harness/runs/onboarding_logs.jsonl")
    users = [
        {"user_id": lg["user_id"], "session_id": lg["session_id"]}
        for lg in logs
        if (lg.get("build") or {}).get("response")
    ]
    jobs = [(cases[i], users[i], ["v1", "v2"] if i % 2 == 0 else ["v2", "v1"]) for i in range(len(cases))]
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(run_case, jobs))
    write_jsonl(EVALS / "_harness/runs/kakao_logs.jsonl", results)
    f04 = [sample("v1", r) for r in results]
    f05 = [sample("v2", r) for r in results]
    write_jsonl(EVALS / "f04-persona-extraction-style/samples.jsonl", f04)
    write_jsonl(EVALS / "f05-v2-persona-extraction/samples.jsonl", f05)
    for name, rows in (("f04", f04), ("f05", f05)):
        print(
            name,
            "n",
            len(rows),
            "errors",
            sum(1 for x in rows if x["error"]),
            "status",
            {s: sum(1 for x in rows if x["output_parsed"]["job_status"] == s) for s in ("succeeded", "failed", None)},
        )


if __name__ == "__main__":
    main()
