"""라이브 인증을 시작하기 전에 확인할 얼굴 위치·거리·정면 자세."""

from __future__ import annotations

from app.features.primary_photo.analyzer import FaceObservation

CENTER_POSITION_STDDEV = 0.02
CENTER_POSITION_MAX_DRIFT = 0.04


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
    if face.width_ratio < 0.18 or face.height_ratio < 0.30:
        return "COME CLOSER"
    if face.width_ratio > 0.50 or face.height_ratio > 0.80:
        return "MOVE BACK"
    if face.center_x < 0.38:
        return "MOVE LEFT"
    if face.center_x > 0.62:
        return "MOVE RIGHT"
    if face.center_y < 0.34:
        return "MOVE DOWN"
    if face.center_y > 0.62:
        return "MOVE UP"
    if abs(face.yaw) > 15 or abs(face.pitch) > 15:
        return "LOOK STRAIGHT AT CAMERA"
    if abs(face.roll) > 12:
        return "KEEP HEAD LEVEL"
    return None
