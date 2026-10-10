"""대표사진 정면·품질 검사 API."""

from fastapi import APIRouter, Depends, File, UploadFile
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.errors import raise_api_error
from app.core.media import read_image
from app.core.security import require_internal_key

from .dependencies import get_primary_photo_service
from .schemas import PrimaryPhotoResponse
from .service import PrimaryPhotoService

router = APIRouter(prefix="/v1/profile-trust/photos", tags=["primary-photo"])


@router.post(
    "/primary/frontal-check",
    response_model=PrimaryPhotoResponse,
    operation_id="checkPrimaryPhotoFrontal",
    summary="대표사진 정면·품질 검사",
    dependencies=[Depends(require_internal_key)],
)
async def check_primary_photo(
    image: UploadFile = File(...),
    service: PrimaryPhotoService = Depends(get_primary_photo_service),
) -> PrimaryPhotoResponse:
    data, _ = await read_image(image, get_settings().max_image_bytes)
    try:
        return await run_in_threadpool(service.inspect, data)
    except Exception as exc:
        raise_api_error(exc)
