from dotenv import load_dotenv

load_dotenv()

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import sentry_sdk

# SENTRY_DSN 이 비어 있으면 SDK 가 꺼진 채로 동작한다 (로컬·테스트·CI 에서 이벤트를 보내지 않는다).
sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN"),
    # 요청 헤더·IP 등 사용자 데이터를 함께 수집한다.
    # https://docs.sentry.io/platforms/python/data-management/data-collected/
    send_default_pii=True,
)

# 앱 로거(logging.getLogger(__name__))에 핸들러가 없으면 WARNING 이상만, 시각도 없이 찍힌다.
# uvicorn 로거는 자기 핸들러가 있고 propagate=False 라 중복되지 않는다.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import JSONResponse
from langfuse import get_client
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.features.persona.api import router as persona_router
from app.features.practice.api import router as practice_router
from app.features.simulation.api import router as simulation_router

description = """
별이삼샵 어플리케이션의 AI API입니다.

## 담당자
- [lena.cho](https://github.com/HyeerinCho)
- [yuta.jeong](https://github.com/hrunj1230)
"""

TAGS_METADATA = [
    {"name": "persona", "description": "사용자 정보를 바탕으로 AI 페르소나를 생성하고 조회합니다."},
    {"name": "practice", "description": "페르소나와의 연습 대화 메시지를 처리합니다."},
    {"name": "simulation", "description": "두 페르소나 간의 대화를 시뮬레이션하고 결과를 반환합니다."},
]


@asynccontextmanager
async def lifespan(_: FastAPI):
    """프로세스 종료 전에 버퍼에 남은 Langfuse 이벤트를 전송한다."""

    yield
    get_client().shutdown()


app = FastAPI(
    title="별이삼샵 AI API",
    description=description,
    version="0.1.0",
    contact={
        "name": "별이삼샵 AI 팀",
        "url": "https://github.com/100-hours-a-week/KTB4-17th-AI",
    },
    openapi_tags=TAGS_METADATA,
    lifespan=lifespan,
    # Nginx 프록시 경로에 따라서
    # root_path="/ai", prefix="/api" 수정
)
api_router = APIRouter(prefix="/ai/api")
api_router.include_router(persona_router)
api_router.include_router(practice_router)
api_router.include_router(simulation_router)


@app.get("/health", status_code=200, summary="헬스 체크", tags=["health"])
@api_router.get("/health", status_code=200, summary="헬스 체크", include_in_schema=False)
async def health_check() -> dict[str, str]:
    """서버 정상 가동 여부를 확인하는 헬스 체크 엔드포인트."""
    return {"status": "ok"}


logger = logging.getLogger(__name__)

# 배포 직후 DB 접속을 확인하는 용도라 오래 기다리지 않는다
DB_READY_TIMEOUT_S = 3.0


@app.get("/health/ready", status_code=200, summary="DB 연결 확인", tags=["health"])
@api_router.get("/health/ready", status_code=200, summary="DB 연결 확인", include_in_schema=False)
async def readiness_check(db: AsyncSession = Depends(get_db)):
    """DATABASE_URL 로 실제 접속해 SELECT 1 을 실행한다. 실패하면 503.

    /health 는 프로세스만 살아 있으면 ok 라 DB 주소가 틀려도 배포가 정상으로 보인다.
    """
    try:
        async with asyncio.timeout(DB_READY_TIMEOUT_S):
            await db.execute(text("SELECT 1"))
    except Exception:
        # 접속 정보가 응답에 섞이지 않도록 원인은 로그로만 남긴다
        logger.exception("readiness: database check failed")
        return JSONResponse(status_code=503, content={"status": "unavailable", "database": "error"})
    return {"status": "ok", "database": "ok"}


# Sentry 연동 확인용. 이벤트 수신을 확인한 뒤 제거한다.
@app.get("/sentry-debug", include_in_schema=False)
async def trigger_error():
    division_by_zero = 1 / 0  # noqa: F841


app.include_router(api_router)
