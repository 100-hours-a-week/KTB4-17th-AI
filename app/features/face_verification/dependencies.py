"""얼굴 인증 기능의 공유 모델 의존성."""

from functools import lru_cache

from app.core.config import get_settings
from app.features.primary_photo.dependencies import get_face_analyzer

from .liveness import ChallengeSigner, LivenessAnalyzer
from .matcher import FaceMatcher
from .service import FaceVerificationService


@lru_cache
def get_face_verification_service() -> FaceVerificationService:
    settings = get_settings()
    analyzer = get_face_analyzer()
    return FaceVerificationService(
        matcher=FaceMatcher(settings),
        signer=ChallengeSigner(settings),
        liveness=LivenessAnalyzer(analyzer),
    )
