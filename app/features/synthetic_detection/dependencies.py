"""AI 생성 사진 판별 공유 모델 의존성."""

from functools import lru_cache

from app.core.config import get_settings

from .service import SyntheticDetectionService


@lru_cache
def get_synthetic_service() -> SyntheticDetectionService:
    return SyntheticDetectionService(get_settings())
