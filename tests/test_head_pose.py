from __future__ import annotations

import threading

import cv2
import numpy as np
import pytest

from app.core.config import Settings
from app.features.face_verification.head import HeadMovement, judge_head_turn, pose_from, read_movement
from app.features.primary_photo.analyzer import FaceAnalyzer, FaceObservation, _estimate_pose
from app.features.primary_photo.head_pose import estimate_head_pose
from scripts.hand_gesture_demo import CALIBRATION_FRAMES, HeadPreview


def projected_landmarks(
    *, pitch=0.0, yaw=0.0, roll=0.0, nose_depth=22.0, size=(480, 640), focal_ratio=1.0
) -> np.ndarray:
    """회전 방향을 아는 가상 얼굴. 추정기의 상수 대신 별도 비율의 얼굴을 투영한다."""
    points = np.array(
        [(0, 0, -nose_depth), (0, 62, -2), (-43, -32, 0), (43, -32, 0), (-26, 30, -6), (26, 30, -6)],
        dtype=np.float64,
    )
    pitch, yaw, roll = np.deg2rad([pitch, -yaw, roll])
    rx = np.array([[1, 0, 0], [0, np.cos(pitch), -np.sin(pitch)], [0, np.sin(pitch), np.cos(pitch)]])
    ry = np.array([[np.cos(yaw), 0, np.sin(yaw)], [0, 1, 0], [-np.sin(yaw), 0, np.cos(yaw)]])
    rz = np.array([[np.cos(roll), -np.sin(roll), 0], [np.sin(roll), np.cos(roll), 0], [0, 0, 1]])
    height, width = size
    focal = max(size) * focal_ratio
    camera = np.array([[focal, 0, width / 2], [0, focal, height / 2], [0, 0, 1]], dtype=np.float64)
    image, _ = cv2.projectPoints(
        points, cv2.Rodrigues(rz @ ry @ rx)[0], np.array([12.0, 18.0, 600.0]), camera, np.zeros(4)
    )
    landmarks = np.zeros((68, 2), dtype=np.float64)
    landmarks[[30, 8, 36, 45, 48, 54]] = image.reshape(-1, 2)
    return landmarks


def face_analyzer(landmarks: np.ndarray) -> FaceAnalyzer:
    analyzer = object.__new__(FaceAnalyzer)
    analyzer.settings = Settings()
    analyzer._lock = threading.Lock()

    class Detector:
        def setInputSize(self, size):
            pass

        def detect(self, image):
            return None, np.array([[200, 80, 240, 300]], dtype=np.float32)

    analyzer._detector = Detector()
    analyzer._fit_landmarks = lambda image, detection: landmarks
    return analyzer


def observe_pose(*, pitch=0.0, yaw=0.0, roll=0.0, nose_depth=22.0):
    landmarks = projected_landmarks(pitch=pitch, yaw=yaw, roll=roll, nose_depth=nose_depth)
    return face_analyzer(landmarks).observe(np.zeros((480, 640, 3), dtype=np.uint8), use_pnp_pose=True)


def test_slight_upward_turn_is_not_lost_to_compressed_nose_ratio():
    center = projected_landmarks()
    upward = projected_landmarks(pitch=-15)
    legacy_delta = _estimate_pose(upward).pitch - _estimate_pose(center).pitch
    assert -12 < legacy_delta < 0  # 구식 거리 비율로는 12도 기준을 통과하지 못하는 재현 입력

    center_pose = pose_from(observe_pose())
    up_pose = pose_from(observe_pose(pitch=-15))
    result = judge_head_turn([center_pose] * 3 + [up_pose] * 3 + [center_pose] * 2, HeadMovement.LOOK_UP)

    assert result.passed
    assert result.peak_delta_deg == pytest.approx(15, abs=0.1)


@pytest.mark.parametrize("roll", [0, 25, -25])
@pytest.mark.parametrize("nose_depth", [16, 22, 28])
@pytest.mark.parametrize(
    ("yaw", "pitch", "expected"),
    [(0, -20, "LOOK_UP"), (0, 20, "LOOK_DOWN"), (20, 0, "TURN_LEFT"), (-20, 0, "TURN_RIGHT")],
)
def test_direction_survives_face_shape_and_roll_variations(yaw, pitch, expected, nose_depth, roll):
    center = pose_from(observe_pose(roll=roll, nose_depth=nose_depth))
    turned = pose_from(observe_pose(yaw=yaw, pitch=pitch, roll=roll, nose_depth=nose_depth))

    assert read_movement(turned, center) == expected
    assert judge_head_turn([center] * 3 + [turned] * 3 + [center] * 2, HeadMovement(expected)).passed


@pytest.mark.parametrize("size", [(480, 640), (720, 1280), (640, 480)])
@pytest.mark.parametrize("focal_ratio", [0.8, 1.0, 1.2])
def test_pitch_direction_with_different_resolutions_and_approximate_camera_focal_length(size, focal_ratio):
    center = estimate_head_pose(projected_landmarks(size=size, focal_ratio=focal_ratio), size)
    up = estimate_head_pose(projected_landmarks(pitch=-20, size=size, focal_ratio=focal_ratio), size)

    assert up.pitch - center.pitch < -12


def test_photo_observation_keeps_legacy_pose_by_default():
    landmarks = projected_landmarks(pitch=-15)
    observation = face_analyzer(landmarks).observe(np.zeros((480, 640, 3), dtype=np.uint8))
    expected = _estimate_pose(landmarks)

    assert (observation.yaw, observation.pitch, observation.roll) == (expected.yaw, expected.pitch, expected.roll)


def test_unusable_pnp_is_not_read_as_neutral_pose():
    observation = face_analyzer(np.zeros((68, 2))).observe(np.zeros((480, 640, 3), dtype=np.uint8), use_pnp_pose=True)

    assert observation.face_count == 1
    assert not observation.pose_valid
    assert pose_from(observation) is None


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_non_finite_landmarks_are_rejected(value):
    landmarks = projected_landmarks()
    landmarks[30, 0] = value

    assert estimate_head_pose(landmarks, (480, 640)) is None


def test_failed_pnp_solver_is_rejected(monkeypatch):
    monkeypatch.setattr(cv2, "solvePnP", lambda *args, **kwargs: (False, None, None))

    assert estimate_head_pose(projected_landmarks(), (480, 640)) is None


def test_demo_completes_slight_look_up_and_return():
    preview = HeadPreview(target=HeadMovement.LOOK_UP)
    center = observe_pose()
    for _ in range(CALIBRATION_FRAMES):
        preview.update(center)
    assert preview.baseline is None
    assert preview.confirm_center()
    assert preview.baseline is not None
    for _ in range(4):
        preview.update(observe_pose(pitch=-15))
    assert preview.stage == "RETURN"
    for _ in range(4):
        preview.update(center)
    assert preview.stage == "DONE"


def test_demo_does_not_finish_with_unusable_face_pose():
    preview = HeadPreview(target=HeadMovement.LOOK_UP)
    center = observe_pose()
    for _ in range(CALIBRATION_FRAMES):
        preview.update(center)
    assert preview.confirm_center()
    for _ in range(4):
        preview.update(observe_pose(pitch=-15))
    preview.update(FaceObservation(face_count=1, pose_valid=False))

    assert preview.stage == "TURN"
