"""선택형 라이브 얼굴 인증 API."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.errors import raise_api_error
from app.core.media import read_image, read_video
from app.core.security import require_internal_key

from .dependencies import get_face_verification_service
from .liveness import InvalidChallenge
from .schemas import VerificationChallengeResponse, VerificationResponse
from .service import FaceVerificationService

router = APIRouter(prefix="/v1/profile-trust/verifications", tags=["face-verification"])


def _raise_verification_error(exc: Exception) -> None:
    if isinstance(exc, InvalidChallenge):
        raise HTTPException(400, str(exc)) from exc
    raise_api_error(exc)


@router.post(
    "/challenges",
    response_model=VerificationChallengeResponse,
    operation_id="createVerificationChallenge",
    summary="라이브 촬영 챌린지 발급",
    status_code=201,
    dependencies=[Depends(require_internal_key)],
)
async def create_verification_challenge(
    service: FaceVerificationService = Depends(get_face_verification_service),
) -> VerificationChallengeResponse:
    return service.create_challenge()


@router.post(
    "/complete",
    response_model=VerificationResponse,
    operation_id="completeFaceVerification",
    summary="라이브 얼굴 인증 완료",
    dependencies=[Depends(require_internal_key)],
)
async def complete_verification(
    challenge_token: str = Form(...),
    primary_photo: UploadFile = File(...),
    live_video: UploadFile = File(...),
    service: FaceVerificationService = Depends(get_face_verification_service),
) -> VerificationResponse:
    settings = get_settings()
    reference, _ = await read_image(primary_photo, settings.max_image_bytes)
    video = await read_video(live_video, settings.max_video_bytes)
    try:
        return await run_in_threadpool(
            service.complete,
            challenge_token,
            reference,
            video,
            live_video.content_type,
        )
    except Exception as exc:
        _raise_verification_error(exc)
