from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from app.core.config import Settings
from app.core.errors import ModelUnavailable
from app.features.face_verification.challenge import CHALLENGE_TARGETS, FrameReading, judge_challenge, random_challenge
from app.features.face_verification.gesture import HandDirection, HandGesture, judge_hold, matches_gesture, read_gesture
from app.features.face_verification.hand import HandLandmarker, PalmDetection, _hand_roi_transform, _ssd_anchors
from app.features.face_verification.head import HeadMovement, HeadPose

# 손바닥이 카메라를 향한 손. x는 오른쪽(검지 쪽), y는 아래, z는 작을수록 카메라에 가깝다.
MCP = {"index": (30, -90), "middle": (10, -95), "ring": (-10, -90), "pinky": (-28, -80)}
SEGMENTS = (40, 25, 20)
FOLD_DEG = (70, 170, 230)  # MCP·PIP·DIP 누적 굽힘
THUMB_CMC = np.array([20.0, -20.0, 0.0])


def _finger(name: str, extended: bool) -> list[np.ndarray]:
    point = np.array([*MCP[name], 0.0])
    scale = 0.8 if name == "pinky" else 1.0
    points = [point]
    for length, fold in zip(SEGMENTS, FOLD_DEG, strict=True):
        theta = 0.0 if extended else math.radians(fold)
        point = point + length * scale * np.array([0.0, -math.cos(theta), -math.sin(theta)])
        points.append(point)
    return points


def _thumb(out: bool, direction_deg: float) -> list[np.ndarray]:
    """direction_deg: 화면 위쪽(0°)에서 검지 쪽으로 기운 각도. out=False면 손바닥을 가로질러 접는다."""
    if out:
        theta = math.radians(direction_deg)
        direction = np.array([math.sin(theta), -math.cos(theta), 0.0])
    else:
        direction = np.array([-25.0, -40.0, -15.0]) / np.linalg.norm([-25.0, -40.0, -15.0])
    return [THUMB_CMC + distance * direction for distance in (0, 30, 55, 75)]


def make_hand(fingers: str, *, thumb_deg: float = 45.0, rotate_deg: float = 0.0, mirror: bool = False) -> np.ndarray:
    """fingers: 엄지·검지·중지·약지·새끼 순서의 펴짐 표시. 예) '01100' = 2."""
    thumb, index, middle, ring, pinky = (flag == "1" for flag in fingers)
    points = [np.zeros(3), *_thumb(thumb, thumb_deg)]
    for name, extended in (("index", index), ("middle", middle), ("ring", ring), ("pinky", pinky)):
        points.extend(_finger(name, extended))
    hand = np.asarray(points)
    theta = math.radians(rotate_deg)
    rotation = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])
    hand[:, :2] = hand[:, :2] @ rotation.T
    if mirror:
        hand[:, 0] *= -1
    return hand + np.array([320.0, 360.0, 0.0])


CASES = [
    ("00000", {}, HandGesture.FIST),
    ("01000", {}, HandGesture.ONE),
    ("01100", {}, HandGesture.TWO),
    ("01110", {}, HandGesture.THREE),
    ("11100", {}, HandGesture.THREE),
    ("01111", {}, HandGesture.FOUR),
    ("11111", {}, HandGesture.OPEN_PALM),
    # 엄지척은 주먹을 옆으로 세운 모양이라, 검지 쪽으로 편 엄지가 위를 향하도록 손 전체를 돌린다.
    ("10000", {"thumb_deg": 70, "rotate_deg": -70}, HandGesture.THUMBS_UP),
    ("11000", {"thumb_deg": 90}, HandGesture.L_SHAPE),
    ("11001", {"thumb_deg": 70}, HandGesture.LOVE_YOU),
    ("10001", {"thumb_deg": 70}, HandGesture.CALL_ME),
]


@pytest.mark.parametrize(("fingers", "options", "expected"), CASES)
@pytest.mark.parametrize("rotation", [0, 90, 180, -90])
def test_down_target_checks_shape_without_direction(fingers, options, expected, rotation):
    points = make_hand(fingers, **options)
    theta = math.radians(rotation)
    points[:, :2] = (points[:, :2] - [320, 360]) @ np.array(
        [[math.cos(theta), math.sin(theta)], [-math.sin(theta), math.cos(theta)]]
    ) + [320, 360]
    reading = read_gesture(points)

    assert matches_gesture(reading, expected, HandDirection.DOWN)
    assert judge_hold([[reading]] * 8, expected, expected_direction=HandDirection.DOWN).passed
    other = HandGesture.ONE if expected != HandGesture.ONE else HandGesture.TWO
    assert not matches_gesture(reading, other, HandDirection.DOWN)


@pytest.mark.parametrize("expected", [HandGesture.FIST, HandGesture.LOVE_YOU, HandGesture.CALL_ME])
@pytest.mark.parametrize("direction", [HandDirection.UP, HandDirection.DOWN, HandDirection.SIDEWAYS])
@pytest.mark.parametrize("rotation", [0, 90, 180, -90])
def test_direction_free_gestures_accept_every_orientation(expected, direction, rotation):
    fingers, options, _ = next(case for case in CASES if case[2] == expected)
    points = make_hand(fingers, **options)
    theta = math.radians(rotation)
    points[:, :2] = (points[:, :2] - [320, 360]) @ np.array(
        [[math.cos(theta), math.sin(theta)], [-math.sin(theta), math.cos(theta)]]
    ) + [320, 360]
    reading = read_gesture(points)

    assert matches_gesture(reading, expected, direction)
    assert judge_hold([[reading]] * 8, expected, expected_direction=direction).passed


def test_random_pool_has_all_thirty_hand_poses_and_four_head_directions():
    assert len(set(CHALLENGE_TARGETS)) == 120
    assert {(target.gesture, target.direction) for target in CHALLENGE_TARGETS} == {
        (gesture, direction)
        for gesture in HandGesture
        for direction in (HandDirection.UP, HandDirection.DOWN, HandDirection.SIDEWAYS)
    }
    assert {target.head for target in CHALLENGE_TARGETS} == set(HeadMovement)


def test_random_challenge_excludes_previous_combination(monkeypatch):
    candidates_seen = []

    def choose(candidates):
        candidates_seen.append(candidates)
        return candidates[-1]

    monkeypatch.setattr("app.features.face_verification.challenge.secrets.choice", choose)
    first = random_challenge()
    second = random_challenge(previous=first)

    assert first != second
    assert len(candidates_seen[0]) == 120
    assert first not in candidates_seen[1] and len(candidates_seen[1]) == 119


@pytest.mark.parametrize("target", CHALLENGE_TARGETS)
def test_every_random_combination_accepts_requested_direction_and_rejects_opposite(target):
    fingers, options, _ = next(case for case in CASES if case[2] == target.gesture)
    points = make_hand(fingers, **options)
    if target.direction == HandDirection.DOWN:
        points[:, 1] = 720 - points[:, 1]
    elif target.direction == HandDirection.SIDEWAYS:
        points[:, :2] = (points[:, :2] - [320, 360]) @ np.array([[0, 1], [-1, 0]]) + [320, 360]
    reading = read_gesture(points)
    turned = {
        HeadMovement.TURN_LEFT: HeadPose(15, 0),
        HeadMovement.TURN_RIGHT: HeadPose(-15, 0),
        HeadMovement.LOOK_UP: HeadPose(0, -15),
        HeadMovement.LOOK_DOWN: HeadPose(0, 15),
    }[target.head]
    poses = [HeadPose(0, 0)] * 3 + [turned] * 2 + [HeadPose(0, 0)]
    frames = [FrameReading(hands=[reading], pose=pose) for pose in poses]

    result = judge_challenge(frames, gesture=target.gesture, direction=target.direction, head=target.head)

    assert reading.gesture == target.gesture
    assert result.passed
    assert result.gesture.expected_direction == target.direction
    other_direction = HandDirection.SIDEWAYS if target.direction == HandDirection.UP else HandDirection.UP
    assert judge_challenge(frames, gesture=target.gesture, direction=other_direction, head=target.head).passed == (
        target.gesture in (HandGesture.FIST, HandGesture.LOVE_YOU, HandGesture.CALL_ME)
    )


@pytest.mark.parametrize(("fingers", "options", "expected"), CASES)
@pytest.mark.parametrize("mirror", [False, True])
def test_read_gesture_classifies_each_hand_shape(fingers, options, expected, mirror):
    reading = read_gesture(make_hand(fingers, mirror=mirror, **options))

    assert reading.fingers == tuple(flag == "1" for flag in fingers)
    assert reading.gesture == expected


@pytest.mark.parametrize(("fingers", "options", "expected"), [case for case in CASES if case[2] != "THUMBS_UP"])
def test_read_gesture_ignores_in_plane_rotation(fingers, options, expected):
    assert read_gesture(make_hand(fingers, rotate_deg=35, **options)).gesture == expected


def test_thumb_only_pointing_sideways_preserves_shape_and_requires_sideways_target():
    reading = read_gesture(make_hand("10000", thumb_deg=70, rotate_deg=20))
    assert reading.gesture == HandGesture.THUMBS_UP
    assert reading.direction == HandDirection.SIDEWAYS
    assert matches_gesture(reading, HandGesture.THUMBS_UP, HandDirection.SIDEWAYS)
    assert not matches_gesture(reading, HandGesture.THUMBS_UP, HandDirection.UP)


@pytest.mark.parametrize(("fingers", "options", "expected"), CASES)
@pytest.mark.parametrize("rotation", [90, -90])
def test_horizontal_hands_accept_both_left_and_right(fingers, options, expected, rotation):
    points = make_hand(fingers, **options)
    theta = math.radians(rotation)
    points[:, :2] = (points[:, :2] - [320, 360]) @ np.array(
        [[math.cos(theta), math.sin(theta)], [-math.sin(theta), math.cos(theta)]]
    ) + [320, 360]
    reading = read_gesture(points)
    assert reading.gesture == expected
    assert reading.direction == HandDirection.SIDEWAYS
    assert matches_gesture(reading, expected, HandDirection.SIDEWAYS)
    assert matches_gesture(reading, expected, HandDirection.UP) == (
        expected in (HandGesture.FIST, HandGesture.LOVE_YOU, HandGesture.CALL_ME)
    )
    assert matches_gesture(reading, expected, HandDirection.DOWN)


def test_l_shape_needs_open_angle_between_thumb_and_index():
    assert read_gesture(make_hand("11000", thumb_deg=35)).gesture is None


def test_unlisted_finger_pattern_is_unknown():
    assert read_gesture(make_hand("00110")).gesture is None


@pytest.mark.parametrize(("fingers", "options", "expected"), CASES)
@pytest.mark.parametrize("mirror", [False, True])
@pytest.mark.parametrize("back_of_hand", [False, True])
def test_upright_gesture_accepts_left_right_and_palm_back(fingers, options, expected, mirror, back_of_hand):
    points = make_hand(fingers, mirror=mirror, **options)
    if back_of_hand:
        # 손 앞뒤 회전으로 깊이 축도 바뀌지만 위아래 방향은 같다.
        points[:, 0] = 640 - points[:, 0]
        points[:, 2] *= -1
    reading = read_gesture(points)

    assert reading.direction == HandDirection.UP
    assert matches_gesture(reading, expected)


@pytest.mark.parametrize(("fingers", "options", "expected"), CASES)
@pytest.mark.parametrize("invert", ["vertical_flip", "half_turn"])
def test_upside_down_gesture_does_not_match_upright_target(fingers, options, expected, invert):
    points = make_hand(fingers, **options)
    points[:, 1] = 720 - points[:, 1]
    if invert == "half_turn":
        points[:, 0] = 640 - points[:, 0]
    reading = read_gesture(points)

    assert reading.direction == HandDirection.DOWN
    direction_free = expected in (HandGesture.FIST, HandGesture.LOVE_YOU, HandGesture.CALL_ME)
    assert matches_gesture(reading, expected) == direction_free
    assert judge_hold([[reading]] * 8, expected).passed == direction_free


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [(55, "UP"), (-55, "UP"), (85, "SIDEWAYS"), (-85, "SIDEWAYS"), (125, "DOWN"), (-125, "DOWN")],
)
def test_hand_direction_allows_tilt_but_separates_horizontal_and_down(rotation, expected):
    reading = read_gesture(make_hand("01100", rotate_deg=rotation))

    assert reading.direction == expected
    assert matches_gesture(reading, HandGesture.TWO) == (expected == "UP")


def test_l_direction_follows_index_not_sideways_thumb():
    reading = read_gesture(make_hand("11000", thumb_deg=90))

    assert reading.direction == HandDirection.UP
    assert reading.direction_tilt_deg == pytest.approx(0)


def test_collapsed_landmarks_have_unknown_direction_and_cannot_match():
    reading = read_gesture(np.zeros((21, 3)))

    assert reading.direction == HandDirection.UNKNOWN
    assert reading.direction_tilt_deg is None
    assert not matches_gesture(reading, HandGesture.FOUR)


def _readings(*gestures):
    return [[read_gesture(make_hand(code, thumb_deg=90))] if code else [] for code in gestures]


def test_judge_hold_passes_when_most_hand_frames_match():
    frames = _readings(*(["11000"] * 7), "01000", "", "")

    hold = judge_hold(frames, HandGesture.L_SHAPE)

    assert hold.passed
    assert (hold.sampled_frames, hold.hand_frames, hold.matched_frames) == (10, 8, 7)
    assert hold.observed == {"L_SHAPE": 7, "ONE": 1}


def test_judge_hold_fails_when_hand_is_missing_in_most_frames():
    hold = judge_hold(_readings("11000", "11000", "", "", "", ""), HandGesture.L_SHAPE)

    assert not hold.passed


def test_judge_hold_fails_when_other_shape_dominates():
    hold = judge_hold(_readings("11000", "01000", "01000", "01000"), HandGesture.L_SHAPE)

    assert not hold.passed
    assert hold.observed == {"ONE": 3, "L_SHAPE": 1}


def test_judge_hold_counts_frame_when_any_hand_matches():
    other = read_gesture(make_hand("00000"))
    target = read_gesture(make_hand("01100"))

    hold = judge_hold([[other, target], [other, target]], HandGesture.TWO)

    assert hold.passed and hold.matched_frames == 2


@pytest.mark.parametrize(("code", "gesture"), [("01100", HandGesture.TWO), ("11000", HandGesture.L_SHAPE)])
def test_judge_hold_reports_upside_down_shape_instead_of_counting_it_as_match(code, gesture):
    points = make_hand(code, thumb_deg=90)
    points[:, 1] = 720 - points[:, 1]
    reading = read_gesture(points)
    hold = judge_hold([[reading]] * 8, gesture)

    assert not hold.passed
    assert hold.expected_direction == HandDirection.UP
    assert hold.matched_frames == 0
    assert hold.observed == {f"{gesture}:DOWN": 8}


def test_judge_hold_requires_shape_and_direction_on_the_same_hand():
    points = make_hand("01100")
    points[:, 1] = 720 - points[:, 1]
    inverted_v = read_gesture(points)
    upright_l = read_gesture(make_hand("11000", thumb_deg=90))

    hold = judge_hold([[upright_l, inverted_v]] * 8, HandGesture.TWO)

    assert not hold.passed and hold.matched_frames == 0
    assert hold.observed == {"TWO:DOWN": 8}


def test_judge_hold_fails_without_frames():
    assert not judge_hold([], HandGesture.FIST).passed


def test_ssd_anchors_match_palm_detection_layout():
    anchors = _ssd_anchors()

    assert anchors.shape == (2016, 2)
    assert np.allclose(anchors[0], [0.5 / 24, 0.5 / 24])
    assert np.allclose(anchors[-1], [11.5 / 12, 11.5 / 12])


@pytest.mark.parametrize("tilt_deg", [0, 40, -75, 150])
def test_hand_roi_transform_stands_hand_upright(tilt_deg):
    theta = math.radians(tilt_deg)
    up = np.array([math.sin(theta), -math.cos(theta)])
    wrist = np.array([300.0, 300.0])
    palm_points = np.array([wrist + up * distance for distance in (0, 20, 60, 50, 40, 10, 15)])
    palm = PalmDetection(box=np.array([260.0, 240.0, 340.0, 320.0]), landmarks=palm_points, score=0.9)

    transform = _hand_roi_transform(palm)
    wrist_crop, middle_crop = (transform @ np.r_[point, 1.0] for point in (palm_points[0], palm_points[2]))

    assert abs(wrist_crop[0] - middle_crop[0]) < 1e-6
    assert middle_crop[1] < wrist_crop[1]


def test_hand_landmarker_requires_model_files(tmp_path: Path):
    settings = Settings(PALM_MODEL_PATH=tmp_path / "missing_palm.onnx", HANDPOSE_MODEL_PATH=tmp_path / "missing.onnx")

    with pytest.raises(ModelUnavailable):
        HandLandmarker(settings)
