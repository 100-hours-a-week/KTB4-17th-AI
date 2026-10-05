"""모바일 웹 영상용 서명 챌린지와 능동형 라이브니스 평가."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil

import cv2
import numpy as np

from app.core.config import Settings
from app.features.primary_photo.analyzer import FaceAnalyzer, FaceObservation

from .schemas import Challenge, FrameDiagnostic, FrameStatus, LivenessResult, VerificationChallengeResponse

MIN_SAMPLED_FRAMES = 8
MIN_VALID_FACE_FRAMES = 6
MAX_CANDIDATE_FRAMES = 3
MAX_SAMPLED_FRAMES = 24
DEFAULT_MAX_ABS_YAW_DEG = 25.0
DEFAULT_MAX_ABS_PITCH_DEG = 25.0
DEFAULT_MAX_ABS_ROLL_DEG = 40.0


class InvalidChallenge(ValueError):
    pass


@dataclass(frozen=True)
class LivenessEvaluation:
    result: LivenessResult
    best_frame: np.ndarray | None
    candidate_frames: tuple[np.ndarray, ...] = ()


class ChallengeSigner:
    def __init__(self, settings: Settings) -> None:
        self.secret = settings.liveness_token_secret.encode()
        self.ttl_seconds = settings.liveness_token_ttl_seconds

    def create(self) -> VerificationChallengeResponse:
        challenges = [Challenge.LOOK_STRAIGHT]
        expires = datetime.now(UTC) + timedelta(seconds=self.ttl_seconds)
        payload = {"challenges": [item.value for item in challenges], "exp": int(expires.timestamp())}
        encoded = _b64(json.dumps(payload, separators=(",", ":")).encode())
        signature = _b64(hmac.new(self.secret, encoded.encode(), hashlib.sha256).digest())
        return VerificationChallengeResponse(
            challenge_token=f"{encoded}.{signature}",
            challenges=challenges,
            expires_at=expires,
        )

    def verify(self, token: str) -> list[Challenge]:
        try:
            encoded, supplied = token.split(".", 1)
            expected = _b64(hmac.new(self.secret, encoded.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(supplied, expected):
                raise InvalidChallenge("유효하지 않은 챌린지 토큰입니다.")
            payload = json.loads(_unb64(encoded))
            if int(payload["exp"]) < int(time.time()):
                raise InvalidChallenge("만료된 챌린지 토큰입니다.")
            return [Challenge(item) for item in payload["challenges"]]
        except InvalidChallenge:
            raise
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise InvalidChallenge("유효하지 않은 챌린지 토큰입니다.") from exc


class LivenessAnalyzer:
    def __init__(self, face_analyzer: FaceAnalyzer) -> None:
        self.face_analyzer = face_analyzer
        settings = getattr(face_analyzer, "settings", None)
        self.max_abs_yaw_deg = getattr(settings, "liveness_max_abs_yaw_deg", DEFAULT_MAX_ABS_YAW_DEG)
        self.max_abs_pitch_deg = getattr(settings, "liveness_max_abs_pitch_deg", DEFAULT_MAX_ABS_PITCH_DEG)
        self.max_abs_roll_deg = getattr(settings, "liveness_max_abs_roll_deg", DEFAULT_MAX_ABS_ROLL_DEG)

    def evaluate(self, video: bytes, suffix: str, challenges: list[Challenge]) -> LivenessEvaluation:
        observations: list[FaceObservation] = []
        sample_records: list[tuple[int, int | None, FaceObservation]] = []
        candidates: list[tuple[float, float, int, np.ndarray]] = []
        sampled = 0

        with tempfile.NamedTemporaryFile(suffix=suffix) as handle:
            handle.write(video)
            handle.flush()
            capture = cv2.VideoCapture(handle.name)
            reported_frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            source_fps = float(capture.get(cv2.CAP_PROP_FPS))
            if not np.isfinite(source_fps) or source_fps <= 0:
                source_fps = 0.0
            target_indices: set[int] | None = None
            if reported_frame_count > 1:
                target_indices = set(_uniform_sample_indices(reported_frame_count))
            index = 0
            try:
                while capture.isOpened():
                    if target_indices is None and sampled >= MAX_SAMPLED_FRAMES:
                        break
                    if target_indices is not None and sampled >= len(target_indices):
                        break
                    ok, frame = capture.read()
                    if not ok:
                        break
                    source_index = index
                    index += 1
                    if target_indices is not None and source_index not in target_indices:
                        continue
                    timestamp_ms = round(source_index / source_fps * 1000) if source_fps else None
                    sampled += 1
                    observation = self.face_analyzer.observe(
                        frame,
                        allow_masked_secondary=True,
                        allow_detection_fallback=True,
                        allow_low_light_enhancement=True,
                    )
                    observations.append(observation)
                    sample_records.append((source_index, timestamp_ms, observation))
                    if self._is_frontal(observation):
                        pose_score = abs(observation.yaw) + abs(observation.pitch) + abs(observation.roll)
                        sharpness = observation.face_blur_variance or 0.0
                        candidates.append((pose_score, -sharpness, sampled - 1, frame.copy()))
            finally:
                capture.release()

        valid = sum(item.face_count == 1 for item in observations)
        frontal = sum(self._is_frontal(item) for item in observations)
        enhanced = sum(item.low_light_enhanced for item in observations)
        required_frontal = _required_frontal_frames(valid)
        completed = _completed_challenges(
            observations,
            challenges,
            max_abs_yaw_deg=self.max_abs_yaw_deg,
            max_abs_pitch_deg=self.max_abs_pitch_deg,
            max_abs_roll_deg=self.max_abs_roll_deg,
        )
        candidates.sort(key=lambda item: (item[0], item[1]))
        passed = sampled >= MIN_SAMPLED_FRAMES and valid >= MIN_VALID_FACE_FRAMES and completed == challenges
        selected_candidates = candidates[:MAX_CANDIDATE_FRAMES] if passed else []
        candidate_ranks = {item[2]: rank for rank, item in enumerate(selected_candidates, start=1)}
        candidate_frames = tuple(item[3] for item in selected_candidates)
        frame_diagnostics = [
            _frame_diagnostic(
                sample_index,
                source_index,
                timestamp_ms,
                observation,
                candidate_ranks,
                max_abs_yaw_deg=self.max_abs_yaw_deg,
                max_abs_pitch_deg=self.max_abs_pitch_deg,
                max_abs_roll_deg=self.max_abs_roll_deg,
            )
            for sample_index, (source_index, timestamp_ms, observation) in enumerate(sample_records)
        ]
        return LivenessEvaluation(
            result=LivenessResult(
                passed=passed,
                completed_challenges=completed,
                sampled_frames=sampled,
                valid_face_frames=valid,
                frontal_face_frames=frontal,
                required_sampled_frames=MIN_SAMPLED_FRAMES,
                required_valid_face_frames=MIN_VALID_FACE_FRAMES,
                required_frontal_face_frames=required_frontal,
                candidate_frame_count=len(candidate_frames),
                low_light_enhanced_frames=enhanced,
                source_frame_count=reported_frame_count if reported_frame_count > 1 else index,
                source_fps=round(source_fps, 3) if source_fps else None,
                max_abs_yaw_deg=self.max_abs_yaw_deg,
                max_abs_pitch_deg=self.max_abs_pitch_deg,
                max_abs_roll_deg=self.max_abs_roll_deg,
                frame_diagnostics=frame_diagnostics,
            ),
            best_frame=candidate_frames[0] if candidate_frames else None,
            candidate_frames=candidate_frames,
        )

    def _is_frontal(self, observation: FaceObservation) -> bool:
        return _is_frontal(
            observation,
            max_abs_yaw_deg=self.max_abs_yaw_deg,
            max_abs_pitch_deg=self.max_abs_pitch_deg,
            max_abs_roll_deg=self.max_abs_roll_deg,
        )


def _frame_diagnostic(
    sample_index: int,
    source_index: int,
    timestamp_ms: int | None,
    observation: FaceObservation,
    candidate_ranks: dict[int, int],
    *,
    max_abs_yaw_deg: float,
    max_abs_pitch_deg: float,
    max_abs_roll_deg: float,
) -> FrameDiagnostic:
    candidate_rank = candidate_ranks.get(sample_index)
    if candidate_rank is not None:
        status = FrameStatus.MATCH_CANDIDATE
    elif observation.face_count == 0:
        status = FrameStatus.NO_FACE
    elif observation.face_count > 1:
        status = FrameStatus.MULTIPLE_FACES
    elif _is_frontal(
        observation,
        max_abs_yaw_deg=max_abs_yaw_deg,
        max_abs_pitch_deg=max_abs_pitch_deg,
        max_abs_roll_deg=max_abs_roll_deg,
    ):
        status = FrameStatus.FRONTAL
    else:
        status = FrameStatus.NON_FRONTAL
    has_single_face = observation.face_count == 1
    return FrameDiagnostic(
        sample_index=sample_index,
        source_frame_index=source_index,
        timestamp_ms=timestamp_ms,
        status=status,
        face_count=observation.face_count,
        detected_face_count=observation.detected_face_count,
        masked_face_count=observation.masked_face_count,
        ignored_background_face_count=observation.ignored_background_face_count,
        yaw=observation.yaw if has_single_face else None,
        pitch=observation.pitch if has_single_face else None,
        roll=observation.roll if has_single_face else None,
        blur_variance=observation.face_blur_variance,
        low_light_enhanced=observation.low_light_enhanced,
        candidate_rank=candidate_rank,
    )


def _uniform_sample_indices(frame_count: int) -> list[int]:
    if frame_count <= 0:
        return []
    target_count = min(MAX_SAMPLED_FRAMES, frame_count)
    return np.linspace(0, frame_count - 1, target_count, dtype=int).tolist()


def _completed_challenges(
    observations: list[FaceObservation],
    expected: list[Challenge],
    *,
    max_abs_yaw_deg: float = DEFAULT_MAX_ABS_YAW_DEG,
    max_abs_pitch_deg: float = DEFAULT_MAX_ABS_PITCH_DEG,
    max_abs_roll_deg: float = DEFAULT_MAX_ABS_ROLL_DEG,
) -> list[Challenge]:
    if expected != [Challenge.LOOK_STRAIGHT]:
        return []
    valid_frames = sum(item.face_count == 1 for item in observations)
    required_frames = _required_frontal_frames(valid_frames)
    frontal_frames = sum(
        _is_frontal(
            item,
            max_abs_yaw_deg=max_abs_yaw_deg,
            max_abs_pitch_deg=max_abs_pitch_deg,
            max_abs_roll_deg=max_abs_roll_deg,
        )
        for item in observations
    )
    return [Challenge.LOOK_STRAIGHT] if frontal_frames >= required_frames else []


def _required_frontal_frames(valid_frames: int) -> int:
    return max(6, ceil(valid_frames * 0.5))


def _is_frontal(
    observation: FaceObservation,
    *,
    max_abs_yaw_deg: float = DEFAULT_MAX_ABS_YAW_DEG,
    max_abs_pitch_deg: float = DEFAULT_MAX_ABS_PITCH_DEG,
    max_abs_roll_deg: float = DEFAULT_MAX_ABS_ROLL_DEG,
) -> bool:
    return (
        observation.face_count == 1
        and abs(observation.yaw) <= max_abs_yaw_deg
        and abs(observation.pitch) <= max_abs_pitch_deg
        and abs(observation.roll) <= max_abs_roll_deg
    )


def video_suffix(content_type: str | None) -> str:
    return {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}.get(content_type or "", ".mp4")


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
