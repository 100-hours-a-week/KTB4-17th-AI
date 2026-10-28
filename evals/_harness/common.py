"""2단계 샘플 생성 공통 도구 — 로컬 평가 서버 호출, 마스킹, JSONL 입출력, Langfuse 조회.

로컬 서버는 평가 전용 DB(ktb_eval)에 붙여 띄운다. 운영 DB 는 건드리지 않는다.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
EVALS = ROOT / "evals"
BASE_URL = os.environ.get("EVAL_BASE_URL", "http://localhost:8077/ai/api")
RUN_TAG = os.environ.get("EVAL_RUN_TAG", "s2")  # 평가 데이터 식별용 접두사


def env_value(key: str) -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].split("#", 1)[0].strip().strip('"')
    return ""


def client(timeout: float = 180.0) -> httpx.Client:
    headers = {}
    key = env_value("INTERNAL_API_KEY")
    if key:
        headers["X-Internal-Api-Key"] = key
    return httpx.Client(base_url=BASE_URL, timeout=timeout, headers=headers)


def eval_id(kind: str) -> str:
    return f"eval-{RUN_TAG}-{kind}-{uuid.uuid4().hex[:8]}"


# ── 마스킹 ────────────────────────────────────────────────
_PHONE = re.compile(r"01[016-9][-\s.]?\d{3,4}[-\s.]?\d{4}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_URL = re.compile(r"https?://\S+|www\.\S+")
_SIG = re.compile(r"(X-Amz-Signature|Signature|sig|token)=[\w%-]+", re.I)
_KEY = re.compile(r"sk-[A-Za-z0-9_-]{10,}|pk-lf-[\w-]+|sk-lf-[\w-]+")


def mask(text: str) -> str:
    if not isinstance(text, str):
        return text
    text = _KEY.sub("[KEY]", text)
    text = _SIG.sub(r"\1=[SIG]", text)
    text = _URL.sub("[URL]", text)
    text = _EMAIL.sub("[EMAIL]", text)
    return _PHONE.sub("[PHONE]", text)


def mask_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return mask(obj)
    if isinstance(obj, list):
        return [mask_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {k: mask_obj(v) for k, v in obj.items()}
    return obj


# ── JSONL ────────────────────────────────────────────────
def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def feature_dir(slug: str) -> Path:
    return EVALS / slug


# ── HTTP 호출 기록 ────────────────────────────────────────
def timed(fn, *args, **kwargs) -> tuple[Any, int, str | None]:
    """(응답, 지연 ms, 오류). 오류여도 예외를 올리지 않는다."""
    started = time.perf_counter()
    try:
        resp = fn(*args, **kwargs)
        ms = int((time.perf_counter() - started) * 1000)
        if resp.status_code >= 400:
            return resp, ms, f"HTTP {resp.status_code}: {resp.text[:300]}"
        return resp, ms, None
    except Exception as e:  # noqa: BLE001
        return None, int((time.perf_counter() - started) * 1000), f"{type(e).__name__}: {e}"


def body(resp) -> Any:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return None


# ── Langfuse ─────────────────────────────────────────────
def langfuse_api():
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from langfuse import get_client

    return get_client().api


def fetch_generations(
    name: str, *, environment: str | list[str] | None = None, limit: int = 100, pages: int = 3, from_start_time=None
) -> list[dict]:
    """generation 관측을 입출력 포함으로 받는다. input/output 은 JSON 이면 파싱한다."""
    api = langfuse_api()
    out, cursor = [], None
    for _ in range(pages):
        r = api.observations.get_many(
            name=name,
            type="GENERATION",
            limit=limit,
            fields="core,basic,io",
            environment=environment,
            cursor=cursor,
            from_start_time=from_start_time,
        )
        for o in r.data:

            def parse(x):
                if isinstance(x, str):
                    try:
                        return json.loads(x)
                    except Exception:  # noqa: BLE001
                        return x
                return x

            out.append(
                {
                    "id": o.id,
                    "trace_id": o.trace_id,
                    "session_id": o.session_id,
                    "user_id": o.user_id,
                    "environment": o.environment,
                    "start_time": str(o.start_time),
                    "model": o.model,
                    "input": parse(o.input),
                    "output": parse(o.output),
                    "latency": o.latency,
                }
            )
        cursor = getattr(r.meta, "cursor", None) or getattr(r.meta, "next_cursor", None)
        if not cursor or not r.data:
            break
    return out
