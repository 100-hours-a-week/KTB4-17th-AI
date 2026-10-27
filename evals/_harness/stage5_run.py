"""5단계 실행기 — 골드셋 케이스를 실제 기능 경로로 돌리고, 코드 검사·Judge 로 채점한다.

    python3 stage5_run.py --split calibration [--only f01,f02] [--no-judge] [--limit 2] [--version local]

- 앱을 이 프로세스 안의 스레드(uvicorn :8078)로 띄운다. DB 는 평가 전용 ktb_eval, Langfuse 환경은 eval-local.
- 엔드포인트가 있는 기능은 2단계와 같은 HTTP 경로로, 단계 함수(심사·수정·리포트 등)는 같은 함수를 직접 부른다.
- LLM 호출은 openai AsyncCompletions.create 한 곳에서 원문을 기록한다 (Langfuse 를 다시 뒤질 필요 없음).
- blind_holdout 은 --allow-holdout 없이는 실행하지 않는다 (6단계 전용).

결과: evals/<slug>/results/<날짜>_<버전>_<split>.json
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EVALS = ROOT / "evals"
PORT = int(os.environ.get("EVAL_PORT", "8078"))


# ── 환경: 평가 DB 로만 붙는다 ─────────────────────────────────────────────
def _env(key: str) -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].split("#", 1)[0].strip().strip('"')
    return ""


_db = _env("DATABASE_URL")
assert _db, "DATABASE_URL 없음"
os.environ["DATABASE_URL"] = re.sub(r"/[A-Za-z0-9_]+$", "/ktb_eval", _db)
assert os.environ["DATABASE_URL"].endswith("/ktb_eval")
os.environ["LANGFUSE_TRACING_ENVIRONMENT"] = "eval-local"
os.environ["EVAL_BASE_URL"] = f"http://localhost:{PORT}/ai/api"
os.environ.setdefault("EVAL_RUN_TAG", "s5")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
os.chdir(ROOT)  # 앱 설정이 현재 폴더의 .env 를 읽는다

import stage3_types as S  # noqa: E402
import stage5_checks as K  # noqa: E402
import stage5_judges as J  # noqa: E402
import uvicorn  # noqa: E402
from common import body, client, read_jsonl, timed  # noqa: E402
from openai.resources.chat.completions import AsyncCompletions  # noqa: E402

from app.main import app  # noqa: E402  (앱이 langfuse openai 래퍼를 먼저 건다)

# ── LLM 원문 기록 ──────────────────────────────────────────────────
REC: list[dict] = []
_orig_create = AsyncCompletions.create


async def _rec_create(self, *args, **kw):
    entry = {
        "name": kw.get("name"),
        "model": kw.get("model"),
        "messages": kw.get("messages"),
        "t0": time.time(),
    }
    try:
        resp = await _orig_create(self, *args, **kw)
    except Exception as e:  # noqa: BLE001
        entry |= {"error": f"{type(e).__name__}: {e}"[:500], "t1": time.time()}
        REC.append(entry)
        raise
    if kw.get("stream"):
        return _StreamRec(resp, entry)
    try:
        msg = resp.choices[0].message
        if getattr(msg, "tool_calls", None):
            entry["output"] = json.dumps(
                {
                    "calls": [
                        {"name": t.function.name, "arguments": _maybe_json(t.function.arguments)}
                        for t in msg.tool_calls
                    ]
                },
                ensure_ascii=False,
            )
        else:
            entry["output"] = msg.content
    except Exception as e:  # noqa: BLE001
        entry["output"] = None
        entry["error"] = f"parse {e}"
    entry["t1"] = time.time()
    REC.append(entry)
    return resp


def _maybe_json(s):
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        return s


class _StreamRec:
    """스트리밍 응답을 그대로 넘기면서 조각을 모아 기록한다."""

    def __init__(self, inner, entry):
        self.inner, self.entry, self.parts = inner, entry, []

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            ch = await self.inner.__anext__()
        except StopAsyncIteration:
            self.entry |= {"output": "".join(self.parts), "t1": time.time()}
            REC.append(self.entry)
            raise
        try:
            d = ch.choices[0].delta.content if ch.choices else None
            if d:
                self.parts.append(d)
        except Exception:  # noqa: BLE001
            pass
        return ch

    def __getattr__(self, k):
        return getattr(self.inner, k)


AsyncCompletions.create = _rec_create

# ── 앱 서버 (스레드) ────────────────────────────────────────────────
LOOP: asyncio.AbstractEventLoop | None = None


def start_server() -> None:
    global LOOP
    cfg = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", lifespan="on")
    server = uvicorn.Server(cfg)

    def run():
        global LOOP
        LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(LOOP)
        LOOP.run_until_complete(server.serve())

    threading.Thread(target=run, daemon=True).start()
    for _ in range(120):
        if server.started:
            return
        time.sleep(0.5)
    raise RuntimeError("평가 서버가 뜨지 않았다")


def in_app(coro, timeout: float = 240):
    """앱 서버와 같은 이벤트 루프에서 코루틴을 실행한다 (DB 엔진을 같이 쓰기 위해)."""
    return asyncio.run_coroutine_threadsafe(coro, LOOP).result(timeout)


def psql(sql: str) -> str:
    return subprocess.run(
        ["psql", "-d", "ktb_eval", "-tAc", sql], capture_output=True, text=True, check=True
    ).stdout.strip()


def q(s: str) -> str:
    return (s or "").replace("'", "''")


class Window:
    """케이스 하나가 도는 동안의 LLM 기록."""

    def __enter__(self):
        self.mark = len(REC)
        return self

    def __exit__(self, *a):
        self.records = REC[self.mark :]

    def by(self, name: str) -> list[dict]:
        return [r for r in REC[self.mark :] if r.get("name") == name]

    def last(self, name: str) -> dict | None:
        xs = self.by(name)
        return xs[-1] if xs else None


def out_of(r: dict | None):
    return None if r is None else r.get("output")


# ── 픽스처 ───────────────────────────────────────────────────────
def uid(kind: str) -> str:
    return f"eval-s5-{kind}-{uuid.uuid4().hex[:8]}"


def clone_user(base_user_id: str, *, keep_style: bool) -> dict:
    """기존 평가 사용자의 온보딩 세션·확정 페르소나를 새 사용자로 복제한다 (원래 사용자는 건드리지 않는다)."""
    new_uid, new_sid, new_pid = uid("kk"), uuid.uuid4().hex, uuid.uuid4().hex
    sid = psql(
        f"select session_id from personas where user_id='{base_user_id}' and is_confirmed order by version desc limit 1"
    )
    psql(
        f"insert into onboarding_sessions (id,user_id,nickname,total_turns,turn_index,pending_topic_id,used_topic_ids,coverage,status,created_at,updated_at,mbti) "
        f"select '{new_sid}','{new_uid}',nickname,total_turns,turn_index,pending_topic_id,used_topic_ids,coverage,status,now(),now(),mbti "
        f"from onboarding_sessions where id='{sid}'"
    )
    style = "conversation_style" if keep_style else "null"
    psql(
        f"insert into personas (id,session_id,user_id,scores,texts,confidence,narrative,version,previous_id,confirmed_at,feedback,created_at,is_confirmed,mbti,source,summaries,conversation_style) "
        f"select '{new_pid}','{new_sid}','{new_uid}',scores,texts,confidence,narrative,1,null,now(),feedback,now(),true,mbti,source,summaries,{style} "
        f"from personas where user_id='{base_user_id}' and is_confirmed order by version desc limit 1"
    )
    return {"user_id": new_uid, "session_id": new_sid}


ME_PRACTICE = "eval-s2-ob-be81a55f"  # 연습대화 '나' 역할 (ob-02). 메시지를 넣을 세션은 매번 새로 만든다.
DEFAULT_PARTNER = "eval-s2-ob-2b99c0a9"  # 2단계 허락 판정 세션의 상대역 (ob-11)
KAKAO_BASE = "eval-s2-ob-9bd92eda"  # 카톡 복제용 (ob-04)


# ── 기능별 실행 ───────────────────────────────────────────────────
SESSION_CACHE: dict[str, dict] = {}
FILLERS = [  # 2단계 topup_onboarding.py 와 같은 무난한 답
    "저는 그냥 편하게 지내는 편이에요",
    "친구들 만나서 밥 먹는 거 좋아해요",
    "연락은 적당히 하는 게 좋아요",
    "천천히 알아가는 게 편해요",
    "진지하게 만날 사람",
] * 2


def run_onboarding(case: dict) -> dict:
    """f01·f03 공통: 답변을 차례로 보내고, 하루의 질문·페르소나를 모은다. 같은 답변 목록은 한 번만 돈다."""
    inp = case["input"]
    key = hashlib.md5(json.dumps(inp["answers"], ensure_ascii=False).encode()).hexdigest()
    if key in SESSION_CACHE:
        return SESSION_CACHE[key]
    c = client(timeout=180)
    user_id = uid("ob")
    nick = inp.get("nickname") or "지민"
    with Window() as w:
        r, ms, err = timed(c.post, "/v1/persona/onboarding/start", json={"nickname": nick, "user_id": user_id})
        turns, error = [], err
        if not err:
            turn = body(r)
            sid = turn["session_id"]
            turns.append({"haru": turn.get("utterance") or turn.get("question"), "user": None})
            for ans in inp["answers"]:
                if turn.get("done"):
                    break
                turns[-1]["user"] = ans
                r, ms, err = timed(
                    c.post,
                    f"/v1/persona/onboarding/{sid}/answer",
                    json={"answer": ans, "turn_index": turn["turn_index"]},
                )
                if err:
                    error = err
                    break
                turn = body(r)
                if not turn.get("done"):
                    turns.append({"haru": turn.get("utterance") or turn.get("question"), "user": None})
            if not turn.get("done") and turn.get("can_finish"):
                timed(c.post, f"/v1/persona/onboarding/{sid}/finish")
            r, bms, berr = timed(c.post, f"/v1/persona/{sid}/build")
            fillers = iter(FILLERS)
            while (
                berr and "not finished" in berr and not turn.get("done")
            ):  # 2단계 topup 과 같다: 무난한 답으로 마저 끝낸다
                ans = next(fillers, None)
                if ans is None:
                    break
                r2, _, err2 = timed(
                    c.post,
                    f"/v1/persona/onboarding/{sid}/answer",
                    json={"answer": ans, "turn_index": turn["turn_index"]},
                )
                if err2:
                    break
                turn = body(r2)
                if turn.get("can_finish") and not turn.get("done"):
                    timed(c.post, f"/v1/persona/onboarding/{sid}/finish")
                r, bms, berr = timed(c.post, f"/v1/persona/{sid}/build")
            persona = body(r) if r is not None else None
        else:
            sid, persona, berr, bms = None, None, err, None
    res = {
        "user_id": user_id,
        "session_id": sid,
        "turns": turns,
        "persona": persona,
        "build_error": berr,
        "build_ms": bms,
        "conv_records": w.by("persona-conversation"),
        "extract_raw": out_of(w.last("persona-extraction")),
        "error": error,
    }
    SESSION_CACHE[key] = res
    return res


def exec_f01(case):
    r = run_onboarding(case)
    dialog = "\n".join(
        f"하루: {t['haru']}" + (f"\n사용자: {t['user']}" if t["user"] is not None else "") for t in r["turns"]
    )
    per_turn, hist = [], []
    raws = [out_of(x) for x in r["conv_records"]]
    for k, t in enumerate(r["turns"]):
        if k > 0:
            per_turn.append(
                {
                    "id": f"{case['id']}-t{k}",
                    "input": {"history": list(hist), "nickname": case["input"].get("nickname")},
                    "output_raw": raws[k - 1] if k - 1 < len(raws) else None,
                    "output_parsed": t["haru"],
                    "latency_ms": None,
                }
            )
        hist.append({"q": t["haru"], "a": t["user"]})
    return {"output_raw": dialog, "output_parsed": dialog, "per_turn": per_turn, "error": r["error"]}


def exec_f03(case):
    r = run_onboarding(case)
    return {
        "output_raw": r["extract_raw"],
        "output_parsed": r["persona"] or {},
        "raw_latency_ms": r["build_ms"],
        "error": r["build_error"] or r["error"],
    }


def exec_f02(case):
    from app.features.persona.agents import TaggingAgent

    inp = case["input"]
    with Window() as w:
        t0 = time.perf_counter()
        tags = in_app(TaggingAgent().tag(inp["question"], inp["answer"]))
        ms = int((time.perf_counter() - t0) * 1000)
    return {
        "output_raw": out_of(w.last("persona-tagging")),
        "output_parsed": tags.model_dump() if tags is not None else None,
        "raw_latency_ms": ms,
        "error": None if tags is not None else "태깅 실패(None)",
    }


def exec_kakao(case, ver: str):
    import run_kakao

    user = clone_user(KAKAO_BASE, keep_style=(ver == "v2"))
    c = client(timeout=180)
    with Window() as w:
        res = run_kakao.run_one(c, ver, case["input"], user)
    job = res.get("job") or {}
    after = res.get("after") or {}
    name = "persona-extraction-style" if ver == "v1" else "v2-persona-extraction"
    return {
        "output_raw": out_of(w.last(name)),
        "output_parsed": {
            "job_status": job.get("status"),
            "analyzed_count": job.get("analyzed_count"),
            "conversation_style": after.get("conversation_style"),
            "scores_after": after.get("scores"),
        },
        "latency_ms": res.get("latency_ms"),
        "error": res.get("submit_error") or (job.get("error") if job.get("status") == "failed" else None),
    }


def practice_session(partner: str, history: list[dict], ver: str = "v2") -> str:
    c = client()
    r, _, err = timed(c.post, f"/{ver}/practice/start", json={"partner_user_id": partner, "me_user_id": ME_PRACTICE})
    if err:
        raise RuntimeError(err)
    sid = body(r)["session_id"]
    if history:
        vals = []
        for k, h in enumerate(history):
            role = "persona" if h["role"] == "persona" else "user"
            vals.append(f"('{sid}', {k}, '{role}', '{q(h['content'])}', 'llm', '{ME_PRACTICE}', now())")
        psql(
            "insert into practice_messages (session_id, index, role, content, source, user_id, created_at) values "
            + ",".join(vals)
        )
        psql(f"update practice_sessions set message_count = {len(history)} where id = '{sid}'")
    return sid


def set_speech(sid: str, mode: str | None) -> None:
    if not mode:
        return
    m = mode.split("→")[0]  # 'asked→declined' 은 '허락을 물은 상태'에서 이번 메시지가 답
    if m in ("asked", "casual", "declined", "formal"):
        psql(
            f"insert into v2_practice_speech_state (session_id, mode, asked_at, updated_at) values ('{sid}', '{m}', now(), now()) "
            f"on conflict (session_id) do update set mode='{m}', asked_at=now(), answered_at=null, updated_at=now()"
        )


def exec_f06(case):
    inp = case["input"]
    ver = "v1" if case["id"].startswith("06-v1-") else "v2"
    sid = practice_session(inp.get("partner_user_id") or DEFAULT_PARTNER, inp.get("history") or [], ver)
    if ver == "v2":
        set_speech(sid, inp.get("speech_mode"))
    c = client()
    with Window() as w:
        if inp.get("opening"):
            r, ms, err = timed(c.post, f"/{ver}/practice/{sid}/opening")
        else:
            r, ms, err = timed(c.post, f"/{ver}/practice/{sid}/messages", json={"message": inp["message"]})
    resp = body(r) if r is not None else {}
    return {
        "version": ver,
        "output_raw": out_of(w.last("practice-reply")),
        "output_parsed": (resp or {}).get("content"),
        "latency_ms": ms,
        "fallback_used": (resp or {}).get("source") == "fallback",
        "validation": (resp or {}).get("validationResult"),
        "error": err,
    }


def exec_f07(case):
    sid = practice_session(DEFAULT_PARTNER, [])
    set_speech(sid, "asked")
    c = client()
    with Window() as w:
        r, ms, err = timed(c.post, f"/v2/practice/{sid}/messages", json={"message": case["input"]["answer"]})
    mode = psql(f"select mode from v2_practice_speech_state where session_id='{sid}'")
    return {
        "output_raw": out_of(w.last("v2-practice-speech-permission")),
        "output_parsed": {"allow": mode == "casual", "mode_after": mode},
        "raw_latency_ms": ms,
        "error": err,
    }


def _pairs(pairs):
    return [{"ai": a, "user": u, "message_id": 1000 + k, "is_overlap": False} for k, (a, u) in enumerate(pairs)]


def exec_f08(case):
    from app.v2.persona.agents import PersonaMemoryExtractor

    inp = case["input"]
    with Window() as w:
        t0 = time.perf_counter()
        try:
            out = in_app(
                PersonaMemoryExtractor().extract(
                    pairs=_pairs(inp["pairs"]),
                    current_date=datetime.now(UTC).date().isoformat(),
                    current_core=inp.get("core_before") or {},
                )
            )
            parsed, err = out.model_dump(), None
        except Exception as e:  # noqa: BLE001
            parsed, err = None, f"{type(e).__name__}: {e}"[:300]
        ms = int((time.perf_counter() - t0) * 1000)
    return {
        "output_raw": out_of(w.last("v2-memory-extract")),
        "output_parsed": parsed,
        "valid_msg_ids": [1000 + k for k in range(len(inp["pairs"]))],
        "latency_ms": ms,
        "error": err,
    }


def exec_f09(case):
    from app.v2.persona.agents import PersonaMemoryExtractor, PersonaMemoryReconcileJudge

    inp = case["input"]
    before = inp.get("facts_before") or []
    with Window() as w:
        t0 = time.perf_counter()
        try:
            ext = in_app(
                PersonaMemoryExtractor().extract(
                    pairs=_pairs(inp["new_pairs"]), current_date=datetime.now(UTC).date().isoformat(), current_core={}
                )
            )
            facts = [{"fact": f.fact, "category": f.category} for f in ext.facts]
            cands = [
                (SimpleNamespace(**{k: b.get(k) for k in ("id", "category", "fact", "status")}), 0.8) for b in before
            ]
            decisions = (
                in_app(PersonaMemoryReconcileJudge().judge_batch([(k, f, [], cands) for k, f in enumerate(facts)]))
                if facts
                else []
            )
            parsed, err = {"new_facts": facts, "decisions": decisions}, None
        except Exception as e:  # noqa: BLE001
            parsed, err = None, f"{type(e).__name__}: {e}"[:300]
        ms = int((time.perf_counter() - t0) * 1000)
    return {
        "output_raw": out_of(w.last("v2-memory-reconcile")) or json.dumps({"decisions": []}),
        "output_parsed": parsed,
        "extract_raw": out_of(w.last("v2-memory-extract")),
        "raw_latency_ms": ms,
        "error": err,
    }


async def _persona(user_id: str):
    from app.core.db import SessionLocal
    from app.features.persona.lookup import load_persona
    from app.features.persona.schemas import PersonaRef

    async with SessionLocal() as db:
        lp = await load_persona(db, PersonaRef(user_id=user_id))
        return lp.response, lp.nickname


def exec_f10(case):
    from app.features.simulation_migration.order import closing_hint_for
    from app.features.simulation_migration.tools import speak_as_a, speak_as_b

    inp = case["input"]
    pa, na = in_app(_persona(inp["me"]["user_id"]))
    pb, nb = in_app(_persona(inp["partner"]["user_id"]))
    na, nb = inp.get("nickname_a") or na, inp.get("nickname_b") or nb
    transcript = []
    for p in inp.get("previous") or []:
        spk, _, text = p.partition(": ")
        transcript.append(("a" if spk.strip() == "a" else "b", text))
    idx, turns = inp["index"], inp["turns"]
    fn, me, other, pm = (speak_as_a, na, nb, pa) if idx % 2 == 0 else (speak_as_b, nb, na, pb)
    with Window() as w:
        t0 = time.perf_counter()
        try:
            res = in_app(
                fn(
                    persona=pm,
                    nickname_self=me,
                    nickname_other=other,
                    transcript=transcript,
                    closing_hint=closing_hint_for(idx, turns),
                    index=idx,
                    turns=turns,
                )
            )
            text, err = res.text, (None if res.text is not None else f"line failed: {res.reason}")
        except Exception as e:  # noqa: BLE001
            text, err = None, f"line failed: {type(e).__name__}: {e}"[:300]
        ms = int((time.perf_counter() - t0) * 1000)
    raws = [out_of(r) for r in w.records if (r.get("name") or "").startswith("simulation-migration")]
    return {"output_raw": raws[-1] if raws else None, "output_parsed": text, "latency_ms": ms, "error": err}


def exec_f11(case):
    from app.features.simulation_migration.report_tool import write_matching_report

    inp = case["input"]
    pa, na = in_app(_persona(inp["me"]["user_id"]))
    pb, nb = in_app(_persona(inp["partner"]["user_id"]))
    names = inp.get("nicknames") or [na, nb]
    state = {
        "persona_a": pa,
        "persona_b": pb,
        "nickname_a": names[0],
        "nickname_b": names[1],
        "transcript": [{"speaker": t["speaker"], "text": t["text"]} for t in inp["transcript"]],
        "run_id": None,
    }
    with Window() as w:
        t0 = time.perf_counter()
        try:
            rep, err = in_app(write_matching_report(state)), None
        except Exception as e:  # noqa: BLE001
            rep, err = None, f"{type(e).__name__}: {e}"[:300]
        ms = int((time.perf_counter() - t0) * 1000)
    rep = rep or {}
    return {
        "output_raw": out_of(w.last("simulation-migration-report")),
        "output_parsed": rep,
        "fallback_used": (rep.get("narrative_source") == "template"),
        "latency_ms": ms,
        "error": err,
    }


def exec_f12(case):
    import run_simulation

    inp = case["input"]
    me, partner = inp["me"], inp["partner"]
    with Window() as w:
        res = run_simulation.run(("v2", 0, me, partner, inp["turns"]))
    db = res.get("db") or {}
    gen = w.by("simulation-v2-generate-text")
    return {
        "output_raw": out_of(gen[0]) if gen else None,
        "output_parsed": None,
        "run_outcome": "succeeded" if (res.get("response") or {}).get("simulation_id") else "failed",
        "run_latency_ms": res.get("latency_ms"),
        "harness_trace": db.get("harness_trace"),
        "error": res.get("error"),
    }


# 시뮬레이션 v2 단계 함수용 맥락 ─────────────────────────────────────
async def _sim_ctx(me_uid: str, partner_uid: str, turns: int, plan_ids: dict | None):
    from app.core.db import SessionLocal
    from app.features.persona.schemas import PersonaRef
    from app.v2.simulation import topics
    from app.v2.simulation.schemas import SimContext, TopicFact, TopicPlan
    from app.v2.simulation.scoring import score_layer
    from app.v2.simulation.service import SimulationV2Service, _clean, _clean_persona

    async with SessionLocal() as db:
        svc = SimulationV2Service(db)
        me = await svc._load("me", PersonaRef(user_id=me_uid))
        partner = await svc._load("partner", PersonaRef(user_id=partner_uid))
        dims, area_scores, _ = score_layer(me.response, partner.response)
        ctx = SimContext(
            me_user_id=me_uid,
            partner_user_id=partner_uid,
            persona_a_id=me.record.id,
            persona_b_id=partner.record.id,
            persona_a=_clean_persona(me.response),
            persona_b=_clean_persona(partner.response),
            name_a=_clean(me.nickname, 20),
            name_b=_clean(partner.nickname, 20),
            area_scores=area_scores,
            dim_scores={d.dimension: d.score for d in dims},
            core_a=await svc.repo.core_profile(me_uid),
            core_b=await svc.repo.core_profile(partner_uid),
        )
        fa = await topics.fetch_facts(db, me_uid)
        fb = await topics.fetch_facts(db, partner_uid)
    if plan_ids:  # 골드셋 새 케이스: 대본의 A-f1… 라벨이 가리키는 fact 를 그대로 쓴다
        by_id = {f.id: (f.category, f.text) for f in [*fa, *fb]}
        # 시뮬레이션의 fact 조회가 일부 범주를 빼서(예: 연애관) 골드셋이 고른 fact 가 없을 수 있다 — DB 에서 직접 읽는다
        need = [i for side in plan_ids.values() for i in side if i not in by_id]
        if need:
            for row in psql(
                f"select id, coalesce(category,''), fact from user_persona_fact where id in ({','.join(map(str, need))})"
            ).splitlines():
                i, cat, txt = row.split("|", 2)
                by_id[int(i)] = (cat, txt)

        def tf(side, k, fid):
            cat, txt = by_id[fid]
            return TopicFact(ref=f"{side}-f{k + 1}", fact_id=fid, owner=side.lower(), category=cat, text=txt)

        plan = TopicPlan(
            mode="shared",
            items=[],
            segments=topics.build_segments(turns, []),
            facts_a=[tf("A", k, i) for k, i in enumerate(plan_ids.get("A") or [])],
            facts_b=[tf("B", k, i) for k, i in enumerate(plan_ids.get("B") or [])],
        )
    else:
        plan = topics.match_topics(fa, fb, turns=turns)
    return ctx, plan


LINE = re.compile(r"^\s*(\d+)\.\s*([^:：]{1,20})\s*[:：]\s*(.*?)(?:\s*\(fact_refs:\s*([^)]*)\))?\s*$")


def parse_script(text: str, name_a: str | None = None):
    from app.v2.simulation.schemas import ScriptLineV2

    out = []
    for ln in (text or "").splitlines():
        m = LINE.match(ln)
        if not m:
            continue
        i = int(m.group(1))
        spk = m.group(2).strip()
        speaker = "a" if spk == "a" else "b" if spk == "b" else ("a" if i % 2 == 0 else "b")
        refs = [r.strip() for r in (m.group(4) or "").split(",") if r.strip()]
        out.append(ScriptLineV2(i=i, speaker=speaker, text=m.group(3).strip() or "…", fact_refs=refs))
    return out


def replay(name: str, sample_id: str, slug: str):
    """2단계에서 본 단계 입력(프롬프트)을 그대로 다시 보낸다 — 부분 대본처럼 함수 입력을 다시 만들 수 없을 때."""
    from auto_checks import LF

    s = J.samples(slug).get(sample_id)
    if not s:
        return None
    for g in LF.get(name, []):
        out = g["output"]
        txt = out.get("content") if isinstance(out, dict) else out
        if txt == s.get("output_raw") or json.dumps(out, ensure_ascii=False) == s.get("output_raw"):
            msgs = g["input"] if isinstance(g["input"], list) else None
            if msgs:
                sysm = next((m["content"] for m in msgs if m.get("role") == "system"), "")
                user = next((m["content"] for m in reversed(msgs) if m.get("role") == "user"), "")
                return sysm, user
    return None


def _sim_inputs(case):
    inp = case["input"]
    return inp["me"]["user_id"], inp["partner"]["user_id"], inp["turns"], inp.get("plan_fact_ids")


def exec_f13(case):
    from app.core.config import get_settings
    from app.v2.simulation import generator
    from app.v2.simulation.llm import call_text

    me, pt, turns, plan_ids = _sim_inputs(case)
    ctx, plan = in_app(_sim_ctx(me, pt, turns, plan_ids))
    with Window() as w:
        try:
            _lines, _ = in_app(
                generator.generate_text(
                    call_text,
                    ctx,
                    plan,
                    turns,
                    correction=case["input"]["correction"],
                    timeout=get_settings().simulation_v2_generate_timeout_s,
                )
            )
            err = None
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"[:300]
    return {"output_raw": out_of(w.last("simulation-v2-regenerate-text")), "output_parsed": None, "error": err}


def exec_f14(case):
    from app.core.config import get_settings
    from app.v2.simulation import verifier
    from app.v2.simulation.llm import call_tools

    s = get_settings()
    inp = case["input"]
    text = inp["script_under_review"]
    with Window() as w:
        if text.startswith("전체 대본"):
            me, pt, turns, plan_ids = _sim_inputs(case)
            ctx, plan = in_app(_sim_ctx(me, pt, turns, plan_ids))
            script = parse_script(text)
            in_app(
                verifier.judge(
                    call_tools,
                    script,
                    ctx,
                    plan,
                    focus=None,
                    timeout=s.simulation_v2_judge_timeout_s,
                    model=s.simulation_v2_judge_model,
                )
            )
            mode = "function"
        else:  # 부분 대본(주변 줄만) — 2단계 프롬프트 그대로 다시 보낸다
            rp = replay("simulation-v2-judge", case["id"].split("-", 1)[1], "f14")
            if rp is None:
                return {"output_raw": None, "error": "재실행할 2단계 프롬프트 없음"}
            in_app(
                call_tools(
                    system=rp[0],
                    user=rp[1],
                    tools=verifier.JUDGE_TOOLS,
                    max_tokens=1200,
                    timeout=s.simulation_v2_judge_timeout_s,
                    name="simulation-v2-judge",
                    model=s.simulation_v2_judge_model,
                )
            )
            mode = "replay"
    return {
        "output_raw": out_of(w.last("simulation-v2-judge")),
        "output_parsed": None,
        "exec_mode": mode,
        "error": None,
    }


ISSUE_MAP = {"judge_mixup": "judge_mixup", "judge_unresponsive": "judge_unresponsive", "perspective": "perspective"}


def exec_f15(case):
    from app.core.config import get_settings
    from app.v2.simulation import repair
    from app.v2.simulation.llm import call_tools
    from app.v2.simulation.schemas import Issue

    s = get_settings()
    inp = case["input"]
    with Window() as w:
        if inp.get("script"):  # 새 케이스 — 함수 그대로
            me, pt, turns, plan_ids = _sim_inputs(case)
            ctx, plan = in_app(_sim_ctx(me, pt, turns, plan_ids))
            script = parse_script(inp["script"])
            issues = []
            for i, body_ in re.findall(r"## (\d+)번 줄.*?\n### 문제\n- (.*?)\n", inp["repair_blocks"], re.S):
                typ = body_.split(":", 1)[0].strip()
                issues.append(
                    Issue(i=int(i), type=ISSUE_MAP.get(typ, "perspective"), severity="local", evidence=body_[:300])
                )
            in_app(repair.repair(call_tools, script, issues, ctx, plan, timeout=s.simulation_v2_repair_timeout_s))
        else:
            rp = replay("simulation-v2-repair", case["id"].split("-", 1)[1], "f15")
            if rp is None:
                return {"output_raw": None, "error": "재실행할 2단계 프롬프트 없음"}
            n = len(re.findall(r"^## \d+번 줄", rp[1], re.M)) or 1
            in_app(
                call_tools(
                    system=rp[0],
                    user=rp[1],
                    tools=[repair.SUBMIT_PATCHES_TOOL],
                    max_tokens=200 + 250 * n,
                    timeout=s.simulation_v2_repair_timeout_s,
                    name="simulation-v2-repair",
                )
            )
    return {"output_raw": out_of(w.last("simulation-v2-repair")), "output_parsed": None, "error": None}


def exec_f16(case):
    from app.core.config import get_settings
    from app.v2.simulation import report
    from app.v2.simulation.llm import call_tools

    s = get_settings()
    me, pt, turns, plan_ids = _sim_inputs(case)
    ctx, plan = in_app(_sim_ctx(me, pt, turns, plan_ids))
    script = parse_script(case["input"]["transcript"])
    with Window() as w:
        try:
            rep, _calls, _ev = in_app(
                report.write_report(
                    call_tools,
                    script,
                    ctx,
                    plan,
                    budget=s.simulation_v2_report_budget,
                    timeout=s.simulation_v2_report_timeout_s,
                )
            )
            parsed, err = rep.model_dump(mode="json"), None
        except Exception as e:  # noqa: BLE001
            parsed, err = None, f"{type(e).__name__}: {e}"[:300]
    return {"output_raw": out_of(w.last("simulation-v2-report")), "output_parsed": parsed, "error": err}


CE_USER, CE_TARGET = "eval-s5-ce-requester", "eval-s5-ce-target"


def exec_f17(case):
    inp = case["input"]
    c = client()
    req = {
        "request_id": str(uuid.uuid4()),
        "room_id": 90000 + int(hashlib.md5(case["id"].encode()).hexdigest()[:4], 16),
        "delegation_id": 90000 + int(hashlib.md5(case["id"].encode()).hexdigest()[4:8], 16),
        "requester_user_id": CE_USER,
        "end_type": inp["end_type"],
        "recent_messages": inp["recent_messages"],
    }
    with Window() as w:
        r, ms, err = timed(c.post, "/v1/chat_end/drafts", json=req)
    b = body(r) if r is not None else {}
    return {
        "output_raw": out_of(w.last("chat-end-drafts")),
        "output_parsed": [t.get("message") for t in (b or {}).get("turns") or []],
        "draft": b,
        "request": req,
        "latency_ms": ms,
        "error": err,
    }


CONV_CACHE: dict[str, list[dict]] = {}


def f18_conv(case) -> tuple[dict, int | None]:
    """새 케이스는 입력의 대화, 2단계 케이스(cm-XX-tK)는 2단계 입력 파일의 대화로 3턴을 돌린다."""
    if case["id"].startswith("18-cm-"):
        m = re.match(r"18-(cm-\d+)-t(\d)", case["id"])
        cm = {x["id"]: x for x in read_jsonl(EVALS / "f18-chat-end-message/inputs.jsonl")}[m.group(1)]
        conv = {x["id"]: x for x in read_jsonl(EVALS / "f17-chat-end-drafts/inputs.jsonl")}[cm["conv_id"]]
        return {
            "key": m.group(1),
            "end_type": cm["end_type"],
            "recent_messages": conv["recent_messages"],
            "target_after_1": cm["target_after_1"],
            "target_after_2": cm["target_after_2"],
        }, int(m.group(2))
    inp = case["input"]
    return {
        "key": case["id"],
        **{k: inp[k] for k in ("end_type", "recent_messages", "target_after_1", "target_after_2")},
    }, None


def exec_f18(case):
    conv, only_turn = f18_conv(case)
    if conv["key"] not in CONV_CACHE:
        d = exec_f17({"id": f"18-{conv['key']}", "input": conv})
        c = client()
        turns = []
        if d["error"] or not d["draft"]:
            turns = [{"error": d["error"] or "초안 없음"}]
        else:
            timed(c.post, f"/v1/chat_end/drafts/{d['draft']['draft_id']}/confirm")
            recent = list(conv["recent_messages"])
            for seq in (1, 2, 3):
                req = {
                    "request_id": str(uuid.uuid4()),
                    "room_id": d["request"]["room_id"],
                    "delegation_id": d["request"]["delegation_id"],
                    "user_id": CE_USER,
                    "target_user_id": CE_TARGET,
                    "end_type": conv["end_type"],
                    "recent_messages": recent[-20:],
                }
                with Window() as w:
                    r, ms, err = timed(c.post, "/v1/chat_end/messages", json=req)
                b = body(r) if r is not None else {}
                turns.append(
                    {
                        "seq": seq,
                        "recent": list(recent),
                        "raw": out_of(w.last("chat-end-message")),
                        "final": b,
                        "latency_ms": ms,
                        "error": err,
                    }
                )
                sent = (b or {}).get("ai_response") or ""
                if sent:
                    recent.append({"speaker": "REQUESTER", "content": sent})
                if seq < 3:
                    recent.append({"speaker": "TARGET", "content": conv[f"target_after_{seq}"]})
        CONV_CACHE[conv["key"]] = turns
    turns = CONV_CACHE[conv["key"]]
    pick = [t for t in turns if only_turn is None or t.get("seq") == only_turn]
    per_turn = [
        {
            "id": f"{case['id']}-s{t.get('seq')}",
            "input": {
                "ai_turn": min(t.get("seq", 1), 3),
                "direction": "",
                "end_type": conv["end_type"],
                "recent_messages": t.get("recent"),
            },
            "output_raw": t.get("raw"),
            "output_parsed": t.get("final") or {},
            "latency_ms": t.get("latency_ms"),
        }
        for t in pick
        if "seq" in t
    ]
    show = "\n\n".join(
        f"[{t['seq']}번째 종료 메시지]\n최근 대화: {json.dumps(t['recent'][-4:], ensure_ascii=False)}\n"
        f"모델 원문: {t['raw']}\n최종: {json.dumps(t['final'], ensure_ascii=False)}"
        for t in pick
        if "seq" in t
    )
    return {
        "output_raw": show,
        "output_parsed": show,
        "per_turn": per_turn,
        "error": next((t.get("error") for t in turns if t.get("error")), None),
    }


EXEC = {
    "f01": exec_f01,
    "f02": exec_f02,
    "f03": exec_f03,
    "f04": lambda c: exec_kakao(c, "v1"),
    "f05": lambda c: exec_kakao(c, "v2"),
    "f06": exec_f06,
    "f07": exec_f07,
    "f08": exec_f08,
    "f09": exec_f09,
    "f10": exec_f10,
    "f11": exec_f11,
    "f12": exec_f12,
    "f13": exec_f13,
    "f14": exec_f14,
    "f15": exec_f15,
    "f16": exec_f16,
    "f17": exec_f17,
    "f18": exec_f18,
}


# ── 채점 ─────────────────────────────────────────────────────────
def code_score(slug: str, case: dict, sample: dict) -> dict:
    if sample.get("per_turn"):  # 세션·대화 단위 케이스: 턴마다 검사하고 한 턴이라도 실패면 실패
        agg: dict[str, dict] = {}
        for t in sample["per_turn"]:
            s2 = {**t, "input": {**case["input"], **t["input"]}}
            for k, v in K.run_checks(slug, case, s2).items():
                cur = agg.get(k)
                if cur is None or (v["pass"] is False and cur["pass"] is not False):
                    agg[k] = {"pass": v["pass"], "reason": f"{t['id']}: {v['reason']}"}
                elif cur["pass"] is None and v["pass"] is True:
                    agg[k] = v
        return agg
    out = K.run_checks(slug, case, sample)
    if slug == "f08" and sample.get("valid_msg_ids") is not None:  # 출처 메시지 id 는 이번 배치 id 안이어야 한다
        d = K.A.as_json(sample.get("output_raw")) or {}
        srcs = [f.get("source_message_id") for f in d.get("facts") or [] if f.get("source_message_id") is not None]
        bad = [x for x in srcs if x not in sample["valid_msg_ids"]]
        out["F-04"] = K.R(not bad, f"배치에 없는 출처 id {bad}" if bad else "출처 id 정상")
    return out


def judge_score(slug: str, case: dict, sample: dict, pool: ThreadPoolExecutor) -> dict:
    if sample.get("error") and not sample.get("output_raw"):
        return {
            t["id"]: {"verdict": "NA", "reasoning": f"실행 오류: {sample['error'][:200]}"} for t in J.judge_types(slug)
        }
    inp = J.view_input(slug, case["input"], sample)
    out = J.view_output(slug, sample)
    orc = J.oracle_text(case)
    futs = {}
    for t in J.judge_types(slug):
        tpl = (J.folder(slug) / "judges" / f"{t['id']}.md").read_text()
        futs[t["id"]] = pool.submit(J.call, J.fill(tpl, J.cut(inp, 6000), J.cut(out, 6000), orc))
    return {k: f.result() for k, f in futs.items()}


def verdicts(slug: str, codes: dict, judges: dict) -> dict:
    v = {}
    for t in S.D[slug]["types"]:
        tid = t["id"]
        if tid in judges:
            v[tid] = judges[tid]["verdict"]
        elif tid in codes and codes[tid]["pass"] is not None:
            v[tid] = "PASS" if codes[tid]["pass"] else "FAIL"
        else:
            v[tid] = "NA"
    return v


def version_tag() -> str:
    h = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "app"], capture_output=True, text=True, cwd=ROOT
    ).stdout.strip()
    return h + ("-dirty" if dirty else "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", required=True, choices=["calibration", "regression", "blind_holdout"])
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--version", default="")
    ap.add_argument("--allow-holdout", action="store_true")
    ap.add_argument("--judge-workers", type=int, default=6)
    ap.add_argument("--cases", default="", help="이 문제들만 다시 돌려 최신 결과 파일에 합친다 (쉼표)")
    a = ap.parse_args()
    if a.split == "blind_holdout" and not a.allow_holdout:
        sys.exit("blind_holdout 은 6단계 최종 측정에서만 실행한다 (--allow-holdout).")
    slugs = [s for s in S.D if not a.only or s in a.only.split(",")]
    start_server()
    ver = a.version or version_tag()
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    pool = ThreadPoolExecutor(a.judge_workers)
    for slug in slugs:
        cases = [c for c in read_jsonl(J.folder(slug) / "goldset.jsonl") if c["split"] == a.split]
        if a.cases:
            want = set(a.cases.split(","))
            cases = [c for c in cases if c["id"] in want]
            if not cases:
                continue
        if a.limit:
            cases = cases[: a.limit]
        # 중간 저장: 멈췄다가 다시 돌리면 끝난 문제는 건너뛴다 (같은 split·버전)
        ckpt = J.folder(slug) / "results" / f".partial_{ver}_{a.split}.jsonl"
        ckpt.parent.mkdir(exist_ok=True)
        rows = read_jsonl(ckpt)
        done = {r["case_id"] for r in rows}
        for c in cases:
            if c["id"] in done:
                continue
            t0 = time.time()
            try:
                sample = {"id": c["id"], "input": c["input"], **EXEC[slug](c)}
            except Exception as e:  # noqa: BLE001
                sample = {
                    "id": c["id"],
                    "input": c["input"],
                    "output_raw": None,
                    "error": f"실행기 오류 {type(e).__name__}: {e}"[:400],
                }
            codes = code_score(slug, c, sample)
            judges = {} if a.no_judge else judge_score(slug, c, sample, pool)
            rows.append(
                {
                    "case_id": c["id"],
                    "split": c["split"],
                    "target_failures": c["target_failures"],
                    "sample": {k: v for k, v in sample.items() if k != "input"},
                    "code": codes,
                    "judge": judges,
                    "verdict": verdicts(slug, codes, judges),
                    "seconds": round(time.time() - t0, 1),
                }
            )
            with ckpt.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rows[-1], ensure_ascii=False) + "\n")
            print(f"{slug} {c['id']} {rows[-1]['seconds']}s err={str(sample.get('error'))[:60]}", flush=True)
        outd = J.folder(slug) / "results"
        outd.mkdir(exist_ok=True)
        p = outd / f"{stamp}_{ver}_{a.split}.json"
        if a.cases:  # 최신 결과 파일의 해당 행만 바꾼다
            prev = sorted(x for x in outd.glob(f"*_{a.split}.json") if "smoke" not in x.name)
            if prev:
                p = prev[-1]
                old = json.loads(p.read_text())
                new = {r["case_id"]: r for r in rows}
                old["rows"] = [new.pop(r["case_id"], r) for r in old["rows"]] + list(new.values())
                p.write_text(json.dumps(old, ensure_ascii=False, indent=1))
                ckpt.unlink(missing_ok=True)
                print("합침", p, flush=True)
                continue
        p.write_text(
            json.dumps(
                {
                    "feature": slug,
                    "split": a.split,
                    "version": ver,
                    "judge_model": J.JUDGE_MODEL if not a.no_judge else None,
                    "run_at": datetime.now().isoformat(),
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=1,
            )
        )
        ckpt.unlink(missing_ok=True)
        print("저장", p, flush=True)


if __name__ == "__main__":
    main()
