"""simulation 플레이그라운드.

    uv run uvicorn dev.simulation.playground:app --reload --port 8001
    → http://localhost:8001

features/simulation 을 그대로 쓰되, 바깥에서 갈아끼운다:
  DB   get_db → SQLite (dev/persona/playground.db — persona 플레이그라운드와 같은 파일)   dependency_overrides
  LLM  simulation.agents._call → _shared.llm (Gemini/Anthropic, 요청별 키)              모듈 속성 교체

페르소나가 있어야 한다. persona 플레이그라운드(포트 8000)에서 둘 이상 만들어 두거나,
여기 마운트된 persona 라우터로 만들어도 된다 (UI 는 시뮬레이션만).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

load_dotenv()

from app.core.db import Base, get_db
from app.features.persona import agents as persona_agents
from app.features.persona import api as persona_api
from app.features.simulation import agents, api
from dev._shared import llm
from dev._shared.app import make_app
from dev._shared.db import SHARED_DB, SQLite
from dev._shared.personas import list_personas

_HERE = Path(__file__).parent

# ── 1. LLM ───────────────────────────────────────────────
agents._call = llm.bind(agents.LLMError)
persona_agents._call = llm.bind(persona_agents.LLMError)

# ── 2. 앱 ────────────────────────────────────────────────
sqlite = SQLite(SHARED_DB)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await sqlite.create_tables(Base)
    yield


app = make_app("simulation playground (dev)", _HERE / "static", lifespan=lifespan)
app.dependency_overrides[get_db] = sqlite.get_db
app.include_router(api.router, dependencies=app.state.deps)
app.include_router(persona_api.router, dependencies=app.state.deps)


@app.get("/personas")
async def personas(db: AsyncSession = Depends(get_db)) -> list[dict]:
    return await list_personas(db)
