"""라이브 영상에서 '지정한 방향으로 고개를 한 번 돌렸다가 돌아왔는가'를 판정한다.

방향은 사용자 기준이다. FE 미리보기는 거울 반전이지만 녹화 영상은 원본이라, 사용자가 자기 왼쪽으로
고개를 돌리면 원본 영상에서는 코가 화면 오른쪽으로 움직여 yaw가 커진다.
라이브 데모는 Face Landmarker의 얼굴 회전을 사용하고, 확인한 정면 대비 변화량으로 본다.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.features.primary_photo.analyzer import FaceObservation

# 시작 정면 대비 이만큼 움직이면 '돌렸다'
HEAD_TURN_MIN_DELTA_DEG = 12.0
# 돌린 뒤 이 범위 안으로 들어오면 '돌아왔다'
HEAD_RETURN_MAX_DELTA_DEG = 6.0
# 기준 자세로 쓰는 앞쪽 얼굴 프레임 수
BASELINE_FRAMES = 3
MIN_TURNED_FRAMES = 2


class HeadMovement(StrEnum):
    LOOK_UP = "LOOK_UP"
    LOOK_DOWN = "LOOK_DOWN"


HEAD_MOVEMENT_LABELS: dict[HeadMovement, str] = {
    HeadMovement.LOOK_UP: "위로 고개 들기",
    HeadMovement.LOOK_DOWN: "아래로 고개 숙이기",
}

# 사용자 기준 상하 방향 → (축, 부호). pitch는 고개를 숙일수록 커지고 들수록 작아진다.
_AXES: dict[HeadMovement, tuple[str, int]] = {
    HeadMovement.LOOK_UP: ("pitch", -1),
    HeadMovement.LOOK_DOWN: ("pitch", 1),
}


@dataclass(frozen=True)
class HeadPose:
    yaw: float
    pitch: float


@dataclass(frozen=True)
class HeadTurn:
    expected: HeadMovement
    face_frames: int
    baseline: HeadPose | None
    peak_delta_deg: float  # 지정 방향으로 가장 많이 움직인 정도
    turned_frames: int
    returned: bool
    strongest: HeadMovement | None  # 네 방향 중 가장 크게 움직인 방향 (기준 미달이면 None)
    passed: bool


def pose_from(observation: FaceObservation) -> HeadPose | None:
    if observation.face_count != 1 or not observation.pose_valid:
        return None
    return HeadPose(yaw=observation.yaw, pitch=observation.pitch)


def movement_delta(pose: HeadPose, baseline: HeadPose, movement: HeadMovement) -> float:
    """기준 자세에서 movement 방향으로 움직인 각도. 반대로 움직이면 음수."""
    axis, sign = _AXES[movement]
    return sign * (getattr(pose, axis) - getattr(baseline, axis))


def read_movement(pose: HeadPose, baseline: HeadPose) -> HeadMovement | None:
    """지금 자세가 기준 대비 상하(pitch) 어느 방향으로 움직였는지 판정한다."""
    movement = max(HeadMovement, key=lambda item: movement_delta(pose, baseline, item))
    return movement if movement_delta(pose, baseline, movement) >= HEAD_TURN_MIN_DELTA_DEG else None


def judge_head_turn(poses: Sequence[HeadPose | None], expected: HeadMovement) -> HeadTurn:
    """시간순 프레임 자세(얼굴이 하나가 아니면 None)로 고개 상하 동작을 판정한다 (복귀 불필요)."""
    faces = [pose for pose in poses if pose is not None]
    if len(faces) < BASELINE_FRAMES + MIN_TURNED_FRAMES:
        return HeadTurn(expected, len(faces), None, 0.0, 0, False, None, False)

    if any(pose is None for pose in poses[:BASELINE_FRAMES]):
        return HeadTurn(expected, len(faces), None, 0.0, 0, False, None, False)
    baseline = HeadPose(
        yaw=statistics.median(pose.yaw for pose in poses[:BASELINE_FRAMES]),
        pitch=statistics.median(pose.pitch for pose in poses[:BASELINE_FRAMES]),
    )
    deltas = [movement_delta(pose, baseline, expected) for pose in faces]
    turned = [index for index, delta in enumerate(deltas) if delta >= HEAD_TURN_MIN_DELTA_DEG]
    consecutive, completed_turn = 0, False
    for pose in poses[BASELINE_FRAMES:]:
        if pose is None:
            consecutive = 0
            continue
        if read_movement(pose, baseline) == expected:
            consecutive += 1
            if consecutive >= MIN_TURNED_FRAMES:
                completed_turn = True
                break
        else:
            consecutive = 0

    peaks = {movement: max(movement_delta(pose, baseline, movement) for pose in faces) for movement in HeadMovement}
    strongest = max(peaks, key=peaks.__getitem__)
    if peaks[strongest] < HEAD_TURN_MIN_DELTA_DEG:
        strongest = None
    passed = completed_turn and strongest == expected

    return HeadTurn(
        expected=expected,
        face_frames=len(faces),
        baseline=baseline,
        peak_delta_deg=round(peaks[expected], 1),
        turned_frames=len(turned),
        returned=passed,
        strongest=strongest,
        passed=passed,
    )
