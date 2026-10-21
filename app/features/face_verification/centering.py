"""라이브 인증을 시작하기 전에 확인할 얼굴 위치·거리·정면 자세."""

from __future__ import annotations

from app.features.primary_photo.analyzer import FaceObservation

CENTER_POSITION_STDDEV = 0.05
CENTER_POSITION_MAX_DRIFT = 0.10
GUIDE_BOUNDS = (0.08, 0.06, 0.92, 0.94)
MIN_FACE_WIDTH_RATIO = 0.05
MIN_FACE_HEIGHT_RATIO = 0.05
MAX_FACE_RATIO = 0.88
MAX_FRONTAL_ANGLE = 25.0


def is_frontal(face: FaceObservation) -> bool:
    return (
        face.face_count == 1
        and face.pose_valid
        and all(abs(angle) <= MAX_FRONTAL_ANGLE for angle in (face.yaw, face.pitch, face.roll))
    )


def centering_hint(face: FaceObservation | None) -> str | None:
    """문제가 없으면 None. 안내 방향은 거울 미리보기 기준이다."""
    if face is None or face.face_count == 0:
        return "SHOW YOUR FACE"
    if face.face_count != 1:
        return "ONLY ONE FACE"
    if not face.pose_valid:
        return "FACE POSE UNAVAILABLE"
    if any(value is None for value in (face.center_x, face.center_y, face.width_ratio, face.height_ratio)):
        return "FACE POSITION UNAVAILABLE"
    if not face.face_in_frame:
        return "KEEP FULL FACE IN VIEW"
    if face.width_ratio < MIN_FACE_WIDTH_RATIO or face.height_ratio < MIN_FACE_HEIGHT_RATIO:
        return "COME CLOSER"
    if face.width_ratio >= MAX_FACE_RATIO and face.height_ratio >= MAX_FACE_RATIO:
        return "MOVE BACK"
    left, top, right, bottom = GUIDE_BOUNDS
    overlap_width = max(
        0, min(face.center_x + face.width_ratio / 2, right) - max(face.center_x - face.width_ratio / 2, left)
    )
    overlap_height = max(
        0, min(face.center_y + face.height_ratio / 2, bottom) - max(face.center_y - face.height_ratio / 2, top)
    )
    if overlap_width * overlap_height < face.width_ratio * face.height_ratio * 0.5 - 1e-9:
        if face.center_x < left:
            return "MOVE LEFT"
        if face.center_x > right:
            return "MOVE RIGHT"
        if face.center_y < top:
            return "MOVE DOWN"
        if face.center_y > bottom:
            return "MOVE UP"
        return "MOVE BACK"
    if abs(face.yaw) > MAX_FRONTAL_ANGLE or abs(face.pitch) > MAX_FRONTAL_ANGLE:
        return "LOOK STRAIGHT AT CAMERA"
    if abs(face.roll) > MAX_FRONTAL_ANGLE:
        return "KEEP HEAD LEVEL"
    return None
