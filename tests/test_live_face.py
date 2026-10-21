from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from app.features.face_verification.centering import centering_hint
from app.features.face_verification.challenge import CHALLENGE_TARGETS
from app.features.face_verification.head import HeadMovement, HeadPose, read_movement
from app.features.face_verification.live_face import observation_from_result, pose_from_matrix
from app.features.primary_photo.analyzer import FaceObservation
from scripts import hand_gesture_demo as demo


def centered_face(**changes) -> FaceObservation:
    face = FaceObservation(
        face_count=1,
        center_x=0.5,
        center_y=0.48,
        width_ratio=0.30,
        height_ratio=0.50,
        pose_method="face_mesh",
    )
    return replace(face, **changes)


def face_matrix(pitch=0.0, yaw=0.0, roll=0.0) -> np.ndarray:
    # MediaPipe 좌표는 x 오른쪽/y 위/z 카메라 쪽이다. 각 축의 변환 부호를 따로 검증한다.
    pitch, yaw, roll = np.deg2rad([pitch, yaw, roll])
    rx = np.array([[1, 0, 0], [0, np.cos(pitch), -np.sin(pitch)], [0, np.sin(pitch), np.cos(pitch)]])
    ry = np.array([[np.cos(yaw), 0, np.sin(yaw)], [0, 1, 0], [-np.sin(yaw), 0, np.cos(yaw)]])
    rz = np.array([[np.cos(roll), -np.sin(roll), 0], [np.sin(roll), np.cos(roll), 0], [0, 0, 1]])
    matrix = np.eye(4)
    matrix[:3, :3] = rz @ ry @ rx
    matrix[2, 3] = -45
    return matrix


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"pitch": -30}, "LOOK_UP"),
        ({"pitch": 25}, "LOOK_DOWN"),
        ({"yaw": 25}, "TURN_LEFT"),
        ({"yaw": -25}, "TURN_RIGHT"),
    ],
)
def test_matrix_orientation_uses_consistent_camera_axes(options, expected):
    pose = pose_from_matrix(face_matrix(**options))

    assert read_movement(HeadPose(pose.yaw, pose.pitch), HeadPose(0, 0)) == expected


def test_rotation_matrix_ignores_uniform_face_scale():
    matrix = face_matrix(pitch=25)
    matrix[:3, :3] *= 1.4
    pose = pose_from_matrix(matrix)

    assert pose.pitch == pytest.approx(25, abs=0.01)
    assert abs(pose.yaw) < 0.01


@pytest.mark.parametrize("matrix", [np.zeros((4, 4)), np.full((4, 4), np.nan), np.eye(3), np.diag([-1, 1, 1, 1])])
def test_invalid_transform_is_not_reported_as_center(matrix):
    assert pose_from_matrix(matrix) is None


def test_detected_face_has_location_and_rotation_from_same_result():
    result = SimpleNamespace(
        face_landmarks=[[SimpleNamespace(x=0.35, y=0.23), SimpleNamespace(x=0.65, y=0.73)]],
        facial_transformation_matrixes=[face_matrix(pitch=-30)],
    )
    face = observation_from_result(result)

    assert face.face_count == 1 and face.pose_valid
    assert face.center_x == pytest.approx(0.50)
    assert face.center_y == pytest.approx(0.48)
    assert face.width_ratio == pytest.approx(0.30)
    assert face.pitch == pytest.approx(-30)
    assert face.pose_method == "face_mesh"


def test_no_matrix_is_not_silently_used_as_frontal_pose():
    result = SimpleNamespace(
        face_landmarks=[[SimpleNamespace(x=0.35, y=0.23), SimpleNamespace(x=0.65, y=0.73)]],
        facial_transformation_matrixes=[],
    )
    assert not observation_from_result(result).pose_valid


@pytest.mark.parametrize("count", [0, 2])
def test_no_face_and_multiple_faces_block_centering(count):
    result = SimpleNamespace(face_landmarks=[[]] * count)
    assert centering_hint(observation_from_result(result)) is not None


@pytest.mark.parametrize(
    ("changes", "hint"),
    [
        ({"center_x": 0.25}, "MOVE LEFT"),
        ({"center_x": 0.75}, "MOVE RIGHT"),
        ({"center_y": 0.30}, "MOVE DOWN"),
        ({"center_y": 0.70}, "MOVE UP"),
        ({"width_ratio": 0.10}, "COME CLOSER"),
        ({"height_ratio": 0.90}, "MOVE BACK"),
        ({"face_in_frame": False}, "KEEP FULL FACE IN VIEW"),
        ({"yaw": 30}, "LOOK STRAIGHT AT CAMERA"),
        ({"pitch": -30}, "LOOK STRAIGHT AT CAMERA"),
        ({"pitch": 25}, "LOOK STRAIGHT AT CAMERA"),
        ({"roll": 20}, "KEEP HEAD LEVEL"),
        ({"pose_valid": False}, "FACE POSE UNAVAILABLE"),
    ],
)
def test_centering_rejects_wrong_position_distance_and_pose(changes, hint):
    face = centered_face(**changes)
    preview = demo.HeadPreview()
    for _ in range(demo.CALIBRATION_FRAMES * 2):
        preview.update(face)

    assert centering_hint(face) == hint
    assert not preview.ready
    assert not preview.confirm_center()
    assert preview.baseline is None


def test_even_stable_centered_face_requires_explicit_confirmation():
    preview = demo.HeadPreview()
    for _ in range(demo.CALIBRATION_FRAMES):
        preview.update(centered_face())

    assert preview.ready
    assert preview.baseline is None
    assert preview.confirm_center()
    assert preview.baseline == HeadPose(0, 0)


def test_slightly_off_center_face_with_small_position_jitter_can_be_confirmed():
    preview = demo.HeadPreview()
    for index in range(demo.CALIBRATION_FRAMES):
        preview.update(centered_face(center_x=0.59 + (0.018 if index % 2 else -0.018), center_y=0.60))

    assert preview.ready
    assert preview.confirm_center()


def test_face_lost_before_enter_invalidates_prepared_center():
    preview = demo.HeadPreview()
    for _ in range(demo.CALIBRATION_FRAMES):
        preview.update(centered_face())
    preview.update(FaceObservation(face_count=0))

    assert not preview.confirm_center()
    assert preview.baseline is None


def test_moving_within_center_box_still_blocks_confirmation():
    preview = demo.HeadPreview()
    for index in range(demo.CALIBRATION_FRAMES):
        preview.update(centered_face(center_x=0.44 if index % 2 else 0.56))

    assert not preview.ready and not preview.confirm_center()


def test_calibration_reset_requires_centering_again():
    preview = demo.HeadPreview()
    for _ in range(demo.CALIBRATION_FRAMES):
        preview.update(centered_face())
    assert preview.confirm_center()
    preview.restart(calibrate=True)

    assert preview.baseline is None
    assert not preview.ready and not preview.confirm_center()


def test_single_yaw_spike_during_downward_movement_is_filtered():
    preview = demo.HeadPreview(target=HeadMovement.LOOK_DOWN)
    for _ in range(demo.CALIBRATION_FRAMES):
        preview.update(centered_face())
    assert preview.confirm_center()
    for _ in range(3):
        preview.update(centered_face(pitch=20))
    preview.update(centered_face(yaw=50, pitch=20))

    assert preview.filtered_pose.yaw == 0
    assert read_movement(preview.filtered_pose, preview.baseline) == HeadMovement.LOOK_DOWN


def test_similar_axis_changes_are_uncertain_not_arbitrarily_left_or_right():
    assert read_movement(HeadPose(20, 18), HeadPose(0, 0)) is None


def test_camera_blocks_challenge_controls_until_center_confirmed(monkeypatch):
    calls = {"random": 0, "hand": 0, "announcements": 0}
    observations = iter([centered_face(center_x=0.25)] * 15 + [centered_face()] * 18)
    keys = [0] * 33
    keys[0], keys[1], keys[14], keys[29], keys[30], keys[31], keys[32] = (
        32,
        ord("n"),
        13,
        13,
        ord("n"),
        ord("r"),
        ord("q"),
    )
    keys = iter(keys)
    capture = SimpleNamespace(
        set=lambda *args: None,
        isOpened=lambda: True,
        read=lambda: (True, np.zeros((480, 640, 3), dtype=np.uint8)),
        release=lambda: None,
    )

    def choose(previous=None):
        calls["random"] += 1
        return CHALLENGE_TARGETS[0 if previous is None else 1]

    def detect(frame):
        calls["hand"] += 1
        return []

    def announce(*args):
        calls["announcements"] += 1

    monkeypatch.setattr(cv2, "VideoCapture", lambda camera: capture)
    monkeypatch.setattr(cv2, "VideoWriter", lambda *args: pytest.fail("Recording started before centering"))
    monkeypatch.setattr(cv2, "imshow", lambda *args: None)
    monkeypatch.setattr(cv2, "waitKey", lambda *args: next(keys))
    monkeypatch.setattr(cv2, "destroyAllWindows", lambda: None)
    monkeypatch.setattr(demo, "random_challenge", choose)
    monkeypatch.setattr(demo, "announce_target", announce)

    demo.run_camera(
        SimpleNamespace(detect=detect),
        SimpleNamespace(observe=lambda frame: next(observations)),
        0,
        None,
        None,
        None,
        random_mode=True,
    )

    assert calls == {"random": 2, "hand": 3, "announcements": 2}
