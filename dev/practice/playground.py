"""practice 플레이그라운드.

    uv run uvicorn dev.practice.playground:app --reload --port 8002
    → http://localhost:8002

features/practice 를 그대로 쓰되, 바깥에서 갈아끼운다:
  DB   get_db → SQLite (dev/persona/playground.db — persona 플레이그라운드와 같은 파일)   dependency_overrides
  LLM  practice.agents._stream → _shared.llm.bind_stream (비스트리밍 호출을 조각내서 냄)   모듈 속성 교체

SSE 자체(start → delta… → done)는 프로덕션 코드 그대로 흐른다. 조각이 나오는 방식만 다르다.
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
from app.features.practice import agents, api
from dev._shared import llm
from dev._shared.app import make_app
from dev._shared.db import SHARED_DB, SQLite
from dev._shared.personas import list_personas

_HERE = Path(__file__).parent

# ── 1. LLM ───────────────────────────────────────────────
agents._stream = llm.bind_stream(agents.LLMError)
persona_agents._call = llm.bind(persona_agents.LLMError)

# ── 2. 앱 ────────────────────────────────────────────────
sqlite = SQLite(SHARED_DB)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await sqlite.create_tables(Base)
    yield


app = make_app("practice playground (dev)", _HERE / "static", lifespan=lifespan)
app.dependency_overrides[get_db] = sqlite.get_db
app.include_router(api.router, dependencies=app.state.deps)
app.include_router(persona_api.router, dependencies=app.state.deps)


@app.get("/personas")
async def personas(db: AsyncSession = Depends(get_db)) -> list[dict]:
    return await list_personas(db)
