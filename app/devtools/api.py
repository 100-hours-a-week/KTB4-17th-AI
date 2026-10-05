"""브라우저 기반 로컬 기능 테스트 페이지."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["devtools"])
TEST_PAGE = Path(__file__).with_name("profile_trust_test.html")


@router.get("/test", include_in_schema=False)
async def test_page() -> FileResponse:
    return FileResponse(TEST_PAGE)
