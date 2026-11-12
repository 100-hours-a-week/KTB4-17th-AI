"""21점 손 랜드마크로 손 모양을 규칙 판정한다.

손가락이 펴졌는지는 관절 굽힘 각도로, 엄지는 손바닥 바깥(검지 쪽)으로 얼마나 나왔는지로 본다.
손 모양은 좌우 반전·손바닥/손등과 무관하게 읽고, 위/아래/가로 방향은 따로 구분한다.
주먹·사랑해·전화 모양과 아래 방향 챌린지는 방향을 검사하지 않는다.
그 외에는 안내한 방향과 같아야 일치한다. 가로는 왼쪽·오른쪽을 모두 허용한다.

좌표는 화면 좌표(x, y + x와 같은 축척의 상대 깊이)를 쓴다. 모델의 world 좌표는 손바닥을 몸에 댄
평평한 손도 40~50° 굽은 것으로 읽어 펴짐 판정에 쓸 수 없었다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

WRIST = 0
THUMB = (1, 2, 3, 4)
INDEX = (5, 6, 7, 8)
MIDDLE = (9, 10, 11, 12)
RING = (13, 14, 15, 16)
PINKY = (17, 18, 19, 20)

# 검지~새끼: MCP·PIP·DIP 굽힘 합이 이 값보다 작으면 편 손가락
FINGER_EXTENDED_MAX_BEND_DEG = 80.0
# 엄지 끝이 검지 뿌리보다 손바닥 폭의 이 비율 이상 바깥에 있으면 편 엄지
THUMB_EXTENDED_MIN_REACH = 0.3
# L: 엄지와 검지가 이루는 최소 각도
L_SHAPE_MIN_ANGLE_DEG = 45.0
# 화면 위/아래에서 이 각도 이내인 경우에만 해당 방향으로 분류한다. 수평 근처는 별도로 둔다.
HAND_DIRECTION_MAX_TILT_DEG = 60.0


class HandGesture(StrEnum):
    FIST = "FIST"
    ONE = "ONE"
    TWO = "TWO"
    THREE = "THREE"
    FOUR = "FOUR"
    OPEN_PALM = "OPEN_PALM"
    THUMBS_UP = "THUMBS_UP"
    L_SHAPE = "L_SHAPE"
    LOVE_YOU = "LOVE_YOU"
    CALL_ME = "CALL_ME"


class HandDirection(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    SIDEWAYS = "SIDEWAYS"
    UNKNOWN = "UNKNOWN"


GESTURE_LABELS: dict[HandGesture, str] = {
    HandGesture.FIST: "✊ 주먹",
    HandGesture.ONE: "1 (검지)",
    HandGesture.TWO: "2 (검지·중지)",
    HandGesture.THREE: "3 (검지·중지·약지)",
    HandGesture.FOUR: "4 (엄지만 접기)",
    HandGesture.OPEN_PALM: "✋ 손바닥",
    HandGesture.THUMBS_UP: "👍 엄지척",
    HandGesture.L_SHAPE: "L (엄지·검지)",
    HandGesture.LOVE_YOU: "🤟 사랑해",
    HandGesture.CALL_ME: "🤙 전화",
}

# (엄지, 검지, 중지, 약지, 새끼) 펴짐 → 손 모양. 방향·각도 조건이 붙는 모양은 _classify에서 따로 본다.
_PATTERNS: dict[tuple[bool, bool, bool, bool, bool], HandGesture] = {
    (False, False, False, False, False): HandGesture.FIST,
    (False, True, False, False, False): HandGesture.ONE,
    (False, True, True, False, False): HandGesture.TWO,
    (False, True, True, True, False): HandGesture.THREE,
    # 엄지·검지·중지로 3을 세는 사람도 있다.
    (True, True, True, False, False): HandGesture.THREE,
    (False, True, True, True, True): HandGesture.FOUR,
    (True, True, True, True, True): HandGesture.OPEN_PALM,
    (True, False, False, False, False): HandGesture.THUMBS_UP,
    (True, True, False, False, False): HandGesture.L_SHAPE,
    (True, True, False, False, True): HandGesture.LOVE_YOU,
    (True, False, False, False, True): HandGesture.CALL_ME,
}


@dataclass(frozen=True)
class GestureReading:
    gesture: HandGesture | None
    fingers: tuple[bool, bool, bool, bool, bool]  # 엄지, 검지, 중지, 약지, 새끼
    finger_bend_deg: tuple[float, float, float, float]  # 검지~새끼 굽힘 합
    thumb_reach: float
    thumb_index_angle_deg: float
    thumb_tilt_deg: float
    direction: HandDirection
    direction_tilt_deg: float | None

    @property
    def label(self) -> str:
        shape = str(self.gesture or "UNKNOWN")
        return shape if self.direction == HandDirection.UP else f"{shape}:{self.direction}"


@dataclass(frozen=True)
class GestureHold:
    expected: HandGesture
    sampled_frames: int
    hand_frames: int
    matched_frames: int
    observed: dict[str, int]
    passed: bool
    expected_direction: HandDirection


def read_gesture(points: np.ndarray) -> GestureReading:
    """points: HandLandmarks.image_points (21, 3)."""
    points = np.asarray(points, dtype=np.float64)[:, :3]

    bends = tuple(_finger_bend(points, joints) for joints in (INDEX, MIDDLE, RING, PINKY))
    thumb_reach = _thumb_reach(points)
    fingers = (
        thumb_reach >= THUMB_EXTENDED_MIN_REACH,
        *(bend < FINGER_EXTENDED_MAX_BEND_DEG for bend in bends),
    )
    thumb_index_angle = _angle(points[THUMB[3]] - points[THUMB[1]], points[INDEX[3]] - points[INDEX[0]])
    # 화면 y축은 아래로 증가하므로 위쪽은 (0, -1)
    thumb_tilt = _angle(points[THUMB[3], :2] - points[THUMB[1], :2], np.array([0.0, -1.0]))
    direction, direction_tilt = _read_direction(points, fingers)
    return GestureReading(
        gesture=_classify(fingers, thumb_index_angle),
        fingers=fingers,
        finger_bend_deg=tuple(round(bend, 1) for bend in bends),
        thumb_reach=round(thumb_reach, 3),
        thumb_index_angle_deg=round(thumb_index_angle, 1),
        thumb_tilt_deg=round(thumb_tilt, 1),
        direction=direction,
        direction_tilt_deg=round(direction_tilt, 1) if direction_tilt is not None else None,
    )


def requires_hand_direction(gesture: HandGesture, direction: HandDirection) -> bool:
    return direction != HandDirection.DOWN and gesture not in (
        HandGesture.FIST,
        HandGesture.LOVE_YOU,
        HandGesture.CALL_ME,
    )


def matches_gesture(
    reading: GestureReading,
    expected: HandGesture,
    expected_direction: HandDirection = HandDirection.UP,
) -> bool:
    """손 모양은 항상 검사하고, 방향 검사가 필요한 챌린지만 요청 방향과 비교한다."""
    return reading.gesture == expected and (
        not requires_hand_direction(expected, expected_direction) or reading.direction == expected_direction
    )


def judge_hold(
    frames: Sequence[Sequence[GestureReading]],
    expected: HandGesture,
    *,
    expected_direction: HandDirection = HandDirection.UP,
    min_hand_ratio: float = 0.5,
    min_match_ratio: float = 0.6,
) -> GestureHold:
    """영상 프레임마다 읽은 손 모양으로 '지정한 모양을 유지했는가'를 판정한다.

    한 프레임에 손이 여러 개면 모양과 필요한 방향 조건을 만족하는 손이 하나라도 있으면 일치로 센다.
    """
    hand_frames = [readings for readings in frames if readings]
    expected_label = expected.value if expected_direction == HandDirection.UP else f"{expected}:{expected_direction}"
    labels = [
        expected_label
        if any(matches_gesture(reading, expected, expected_direction) for reading in readings)
        else next((reading.label for reading in readings if reading.gesture == expected), readings[0].label)
        for readings in hand_frames
    ]
    matched = labels.count(expected_label)
    observed = Counter(labels)
    passed = (
        bool(frames)
        and len(hand_frames) >= len(frames) * min_hand_ratio
        and matched >= len(hand_frames) * min_match_ratio
    )
    return GestureHold(
        expected=expected,
        sampled_frames=len(frames),
        hand_frames=len(hand_frames),
        matched_frames=matched,
        observed=dict(observed.most_common()),
        passed=passed,
        expected_direction=expected_direction,
    )


def _read_direction(
    points: np.ndarray,
    fingers: tuple[bool, bool, bool, bool, bool],
) -> tuple[HandDirection, float | None]:
    # V는 검지·중지, L은 검지 방향으로 본다. 옆으로 뻗은 엄지가 방향 판정을 흔들지 않게 한다.
    vectors = [
        points[joints[-1], :2] - points[joints[0], :2]
        for extended, joints in zip(fingers[1:], (INDEX, MIDDLE, RING, PINKY), strict=True)
        if extended
    ]
    if not vectors:
        # 엄지척은 엄지, 주먹은 손목→중지 뿌리를 기준으로 삼는다.
        start, end = (THUMB[1], THUMB[-1]) if fingers[0] else (WRIST, MIDDLE[0])
        vectors = [points[end, :2] - points[start, :2]]
    lengths = [float(np.linalg.norm(vector)) for vector in vectors]
    if any(length <= 1e-9 for length in lengths):
        return HandDirection.UNKNOWN, None
    direction = np.mean([vector / length for vector, length in zip(vectors, lengths, strict=True)], axis=0)
    if float(np.linalg.norm(direction)) <= 1e-9:
        return HandDirection.UNKNOWN, None
    tilt = _angle(direction, np.array([0.0, -1.0]))
    if tilt <= HAND_DIRECTION_MAX_TILT_DEG:
        return HandDirection.UP, tilt
    if tilt >= 180 - HAND_DIRECTION_MAX_TILT_DEG:
        return HandDirection.DOWN, tilt
    return HandDirection.SIDEWAYS, tilt


def _classify(
    fingers: tuple[bool, bool, bool, bool, bool],
    thumb_index_angle: float,
) -> HandGesture | None:
    gesture = _PATTERNS.get(fingers)
    # THUMBS_UP은 엄지만 편 모양의 식별자다. 위/아래/가로는 별도 방향 판정에 맡긴다.
    if gesture == HandGesture.L_SHAPE and thumb_index_angle < L_SHAPE_MIN_ANGLE_DEG:
        return None
    return gesture


def _finger_bend(points: np.ndarray, joints: tuple[int, int, int, int]) -> float:
    chain = [points[WRIST], *(points[index] for index in joints)]
    segments = [chain[i + 1] - chain[i] for i in range(len(chain) - 1)]
    return float(sum(_angle(segments[i], segments[i + 1]) for i in range(len(segments) - 1)))


def _thumb_reach(points: np.ndarray) -> float:
    """엄지 끝이 검지 뿌리보다 검지 쪽 바깥으로 얼마나 나왔는지(손바닥 폭 대비)."""
    across = points[INDEX[0]] - points[PINKY[0]]
    palm_width = float(np.linalg.norm(across))
    if palm_width <= 1e-9:
        return 0.0
    return float(np.dot(points[THUMB[3]] - points[INDEX[0]], across / palm_width) / palm_width)


def _angle(first: np.ndarray, second: np.ndarray) -> float:
    norm = float(np.linalg.norm(first) * np.linalg.norm(second))
    if norm <= 1e-12:
        return 0.0
    return float(np.degrees(np.arccos(np.clip(np.dot(first, second) / norm, -1.0, 1.0))))
