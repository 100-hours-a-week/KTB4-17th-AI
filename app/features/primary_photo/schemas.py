"""대표사진 정면·품질 검사 응답 모델."""

from enum import StrEnum

from pydantic import BaseModel


class PhotoDecision(StrEnum):
    PASS = "PASS"
    RETRY = "RETRY"


class ReasonCode(StrEnum):
    NO_FACE = "NO_FACE"
    MULTIPLE_FACES = "MULTIPLE_FACES"
    NON_FRONTAL_FACE = "NON_FRONTAL_FACE"
    IMAGE_TOO_SMALL = "IMAGE_TOO_SMALL"
    IMAGE_TOO_BLURRY = "IMAGE_TOO_BLURRY"
    IMAGE_TOO_DARK = "IMAGE_TOO_DARK"
    IMAGE_TOO_BRIGHT = "IMAGE_TOO_BRIGHT"


class Pose(BaseModel):
    yaw: float
    pitch: float
    roll: float


class Quality(BaseModel):
    width: int
    height: int
    face_count: int
    detected_face_count: int
    masked_face_count: int = 0
    ignored_background_face_count: int = 0
    detection_method: str = "yunet"
    face_area_ratio: float | None = None
    blur_variance: float
    global_blur_variance: float
    brightness: float
    pose: Pose | None = None


class PrimaryPhotoResponse(BaseModel):
    decision: PhotoDecision
    reason_codes: list[ReasonCode]
    quality: Quality
