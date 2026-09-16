"""플레이그라운드 앱 뼈대. 기능별 playground.py 가 여기에 라우터를 얹는다."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse

from . import llm


def make_app(
    title: str,
    static_dir: Path,
    header_deps: list = (),
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[Any]] | None = None,
) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    deps = [Depends(llm.headers), *header_deps]
    app = FastAPI(title=title, lifespan=lifespan)
    app.state.deps = deps  # include_router 할 때 같이 넘기라고

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.post("/check", dependencies=deps)
    async def check() -> dict:
        return await llm.check()

    @app.get("/meta/llm")
    async def meta_llm() -> dict:
        return llm.models()

    return app
