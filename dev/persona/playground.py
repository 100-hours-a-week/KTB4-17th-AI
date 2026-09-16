"""persona 플레이그라운드.

    uv run uvicorn dev.persona.playground:app --reload --port 8000
    → http://localhost:8000

features/persona 를 그대로 쓰되, 바깥에서 갈아끼운다:
  DB       api.get_db → SQLite (dev/persona/playground.db)     dependency_overrides
  LLM      agents._call → _shared.llm (Gemini/Anthropic, 요청별 키)  모듈 속성 교체
  태깅     TaggingAgent.tag → X-Tagging: 0 이면 건너뜀 (무료 티어 호출 절약)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path

from fastapi import Depends, FastAPI, Header

from app.features.persona import agents, api
from app.features.persona.models import Base
from app.features.persona.schemas import SCORED, TEXTUAL
from dev._shared import llm
from dev._shared.app import make_app
from dev._shared.db import SQLite

_HERE = Path(__file__).parent

# ── 1. LLM ───────────────────────────────────────────────
# agents 의 _call_json · ConversationAgent.generate 는 모듈 전역 _call 을 호출 시점에 찾는다.
# agents.LLMError 로 던지게 bind 해서 폴백(try/except LLMError)이 그대로 산다.
_llm_call = llm.bind(agents.LLMError)

agents._call = _llm_call

# ── 2. 태깅 스위치 ────────────────────────────────────────
tagging_on: ContextVar[bool] = ContextVar("dev_tagging", default=True)
_original_tag = agents.TaggingAgent.tag


async def _tag_switchable(self, question: str, answer: str):
    if not tagging_on.get():
        return None  # service 가 topic.covers 로 폴백
    return await _original_tag(self, question, answer)


agents.TaggingAgent.tag = _tag_switchable


async def _persona_headers(x_tagging: str | None = Header(default=None)) -> None:
    tagging_on.set(x_tagging != "0")


# ── 3. 앱 ────────────────────────────────────────────────
sqlite = SQLite(_HERE / "playground.db")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await sqlite.create_tables(Base)
    yield


app = make_app(
    "persona playground (dev)",
    _HERE / "static",
    [Depends(_persona_headers)],
    lifespan=lifespan,
)
app.dependency_overrides[api.get_db] = sqlite.get_db
app.include_router(api.router, dependencies=app.state.deps)


@app.get("/meta")
async def meta() -> dict:
    return {
        "models": llm.models(),
        "scored": {k: {"area": d.area, "label": d.label, "low": d.low, "high": d.high} for k, d in SCORED.items()},
        "textual": TEXTUAL,
    }
