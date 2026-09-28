"""연습대화 테스트 페이지 서버 — 개발용.

`index.html` 을 `/` 로 서빙하고 `/ai/api/*` 를 실제 API 서버로 그대로 중계한다.
브라우저 입장에서 같은 출처라 CORS 설정이 필요 없고, 운영 코드(app/)는 건드리지 않는다.
SSE 는 버퍼링 없이 조각 그대로 흘려보낸다.

  # 1) 실제 API
  uv run uvicorn app.main:app --port 8000
  # 2) 테스트 페이지 (API 주소를 바꾸려면 PRACTICE_API=http://host:port)
  uv run uvicorn dev.practice.server:app --port 8770
  # 3) http://localhost:8770
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

TARGET = os.getenv("PRACTICE_API", "http://localhost:8000").rstrip("/")
INDEX = Path(__file__).with_name("index.html")
PASS_HEADERS = ("content-type", "cache-control", "x-accel-buffering")

app = FastAPI(title="연습대화 테스트 페이지")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(INDEX)


@app.api_route("/ai/api/{path:path}", methods=["GET", "POST"], include_in_schema=False)
async def proxy(path: str, request: Request) -> StreamingResponse:
    client = httpx.AsyncClient(timeout=None)
    upstream_request = client.build_request(
        request.method,
        f"{TARGET}/ai/api/{path}",
        params=request.query_params,
        content=await request.body(),
        headers={"content-type": request.headers.get("content-type", "application/json")},
    )
    try:
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as e:
        await client.aclose()
        detail = f"API 서버({TARGET})에 연결할 수 없어요: {type(e).__name__}"
        return JSONResponse({"detail": detail}, status_code=502)

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    headers = {k: v for k, v in upstream.headers.items() if k.lower() in PASS_HEADERS}
    return StreamingResponse(body(), status_code=upstream.status_code, headers=headers)
