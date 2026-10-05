"""라이브 얼굴 인증 API 모델."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class VerificationDecision(StrEnum):
    VERIFIED = "VERIFIED"
    RETRY = "RETRY"
    NOT_VERIFIED = "NOT_VERIFIED"


class Challenge(StrEnum):
    LOOK_STRAIGHT = "LOOK_STRAIGHT"


class ReasonCode(StrEnum):
    NO_FACE = "NO_FACE"
    LIVENESS_FAILED = "LIVENESS_FAILED"
    FACE_MISMATCH = "FACE_MISMATCH"
    FACE_COMPARISON_FAILED = "FACE_COMPARISON_FAILED"


class VerificationChallengeResponse(BaseModel):
    challenge_token: str
    challenges: list[Challenge]
    expires_at: datetime


class FrameStatus(StrEnum):
    NO_FACE = "NO_FACE"
    MULTIPLE_FACES = "MULTIPLE_FACES"
    NON_FRONTAL = "NON_FRONTAL"
    FRONTAL = "FRONTAL"
    MATCH_CANDIDATE = "MATCH_CANDIDATE"


class FrameDiagnostic(BaseModel):
    sample_index: int
    source_frame_index: int
    timestamp_ms: int | None = None
    status: FrameStatus
    face_count: int
    detected_face_count: int
    masked_face_count: int = 0
    ignored_background_face_count: int = 0
    yaw: float | None = None
    pitch: float | None = None
    roll: float | None = None
    blur_variance: float | None = None
    low_light_enhanced: bool = False
    candidate_rank: int | None = None


class LivenessResult(BaseModel):
    passed: bool
    completed_challenges: list[Challenge]
    sampled_frames: int
    valid_face_frames: int
    frontal_face_frames: int = 0
    required_sampled_frames: int = 8
    required_valid_face_frames: int = 6
    required_frontal_face_frames: int = 6
    candidate_frame_count: int = 0
    low_light_enhanced_frames: int = 0
    source_frame_count: int = 0
    source_fps: float | None = None
    sampling_strategy: str = "uniform_max_24"
    max_abs_yaw_deg: float = 25.0
    max_abs_pitch_deg: float = 25.0
    max_abs_roll_deg: float = 40.0
    frame_diagnostics: list[FrameDiagnostic] = Field(default_factory=list)


class MatchDiagnostics(BaseModel):
    threshold: float
    compared_frame_count: int
    frame_similarities: list[float]
    aggregation: str = "median"


class VerificationResponse(BaseModel):
    decision: VerificationDecision
    reason_codes: list[ReasonCode]
    similarity: float | None = None
    liveness: LivenessResult
    model_version: str
    match: MatchDiagnostics | None = None
