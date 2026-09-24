from dotenv import load_dotenv

load_dotenv()

from fastapi import APIRouter, FastAPI

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
app = FastAPI(
    title="별이삼샵 AI API",
    description=description,
    version="0.1.0",
    contact={
        "name": "별이삼샵 AI 팀",
        "url": "https://github.com/100-hours-a-week/KTB4-17th-AI",
    },
    openapi_tags=TAGS_METADATA,
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


app.include_router(api_router)

