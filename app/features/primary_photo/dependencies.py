"""대표사진 기능의 공유 모델 의존성."""

from functools import lru_cache

from app.core.config import get_settings

from .analyzer import FaceAnalyzer
from .service import PrimaryPhotoService


@lru_cache
def get_face_analyzer() -> FaceAnalyzer:
    return FaceAnalyzer(get_settings())


@lru_cache
def get_primary_photo_service() -> PrimaryPhotoService:
    return PrimaryPhotoService(get_face_analyzer())
