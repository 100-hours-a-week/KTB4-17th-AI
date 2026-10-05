"""대표사진 AI 생성 위험 판별 API."""

from fastapi import APIRouter, Depends, File, UploadFile
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.errors import raise_api_error
from app.core.media import read_image
from app.core.security import require_internal_key

from .dependencies import get_synthetic_service
from .schemas import SyntheticResult
from .service import SyntheticDetectionService

router = APIRouter(prefix="/v1/profile-trust/photos", tags=["synthetic-detection"])


@router.post(
    "/primary/synthetic-check",
    response_model=SyntheticResult,
    operation_id="checkPrimaryPhotoSynthetic",
    summary="대표사진 AI 생성 위험 검사",
    dependencies=[Depends(require_internal_key)],
)
async def check_primary_photo_synthetic(
    image: UploadFile = File(...),
    service: SyntheticDetectionService = Depends(get_synthetic_service),
) -> SyntheticResult:
    data, content_type = await read_image(image, get_settings().max_image_bytes)
    try:
        return await run_in_threadpool(service.inspect, data, content_type)
    except Exception as exc:
        raise_api_error(exc)
