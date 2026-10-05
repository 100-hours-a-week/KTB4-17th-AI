"""대표사진 정면·품질 검사 유스케이스."""

from app.core.media import decode_image

from .analyzer import FaceAnalyzer
from .schemas import PhotoDecision, PrimaryPhotoResponse


class PrimaryPhotoService:
    def __init__(self, analyzer: FaceAnalyzer) -> None:
        self.analyzer = analyzer

    def inspect(self, data: bytes) -> PrimaryPhotoResponse:
        analysis = self.analyzer.analyze(decode_image(data))
        reasons = list(analysis.reasons)
        return PrimaryPhotoResponse(
            decision=PhotoDecision.PASS if not reasons else PhotoDecision.RETRY,
            reason_codes=reasons,
            quality=analysis.quality,
        )
