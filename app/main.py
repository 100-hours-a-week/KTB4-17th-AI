from dotenv import load_dotenv

load_dotenv()

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

from fastapi import APIRouter, FastAPI
from langfuse import get_client

from app.core.config import get_settings
from app.devtools.api import router as devtools_router
from app.features.chat_end.api import router as chat_end_router
from app.features.face_verification.api import router as face_verification_router
from app.features.persona.api import router as persona_router
from app.features.practice.api import router as practice_router
from app.features.primary_photo.api import router as primary_photo_router
from app.features.simulation.api import router as simulation_router
from app.features.simulation_migration.api import get_run_manager
from app.features.simulation_migration.api import router as simulation_migration_router
from app.features.synthetic_detection.api import router as synthetic_detection_router

description = """
별이삼샵 어플리케이션의 AI API입니다.

## 담당자
- [lena.cho](https://github.com/HyeerinCho)
- [yuta.jeong](https://github.com/hrunj1230)
"""

TAGS_METADATA = [
    {"name": "persona", "description": "사용자 정보를 바탕으로 AI 페르소나를 생성하고 조회합니다."},
    {"name": "practice", "description": "페르소나와의 연습 대화 메시지를 처리합니다."},
    {"name": "chat-end", "description": "채팅을 마무리하는 메시지 초안과 최종 종료 메시지를 만듭니다."},
    {
        "name": "simulation",
        "description": "LangGraph로 두 페르소나가 한 줄씩 대화하고, 끝난 뒤 대본과 리포트를 반환합니다.",
    },
    {"name": "simulation_old", "description": "예전 한 호출 시뮬레이션입니다. 경로는 /v1/simulation_old 입니다."},
    {"name": "primary-photo", "description": "대표사진의 정면·품질을 검사합니다."},
    {"name": "synthetic-detection", "description": "대표사진의 AI 생성 위험을 검사합니다."},
    {"name": "face-verification", "description": "라이브니스와 동일인 여부를 확인합니다."},
]


@asynccontextmanager
async def lifespan(_: FastAPI):
    """프로세스 종료 전에 RunManager를 정리하고 Langfuse 이벤트를 전송한다."""

    yield
    try:
        await get_run_manager().shutdown()
    finally:
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
api_router.include_router(chat_end_router)
api_router.include_router(simulation_router)
api_router.include_router(simulation_migration_router)
api_router.include_router(primary_photo_router)
api_router.include_router(synthetic_detection_router)
api_router.include_router(face_verification_router)


@app.get("/health", status_code=200, summary="헬스 체크", tags=["health"])
@api_router.get("/health", status_code=200, summary="헬스 체크", include_in_schema=False)
async def health_check() -> dict[str, str]:
    """서버 정상 가동 여부를 확인하는 헬스 체크 엔드포인트."""
    return {"status": "ok"}


# Sentry 연동 확인용. 이벤트 수신을 확인한 뒤 제거한다.
@app.get("/sentry-debug", include_in_schema=False)
async def trigger_error():
    division_by_zero = 1 / 0  # noqa: F841


app.include_router(api_router)
if get_settings().enable_test_ui:
    app.include_router(devtools_router)
