"""손 모양 유지 + 고개 한 번 챌린지를 영상 프레임 단위로 읽고 판정한다 (프로토타입)."""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .gesture import GestureHold, GestureReading, HandDirection, HandGesture, judge_hold, read_gesture
from .hand import HandLandmarker
from .head import HeadMovement, HeadPose, HeadTurn, judge_head_turn, pose_from
from .live_face import LiveFaceAnalyzer

MAX_SAMPLED_FRAMES = 24


@dataclass(frozen=True)
class ChallengeTarget:
    gesture: HandGesture
    direction: HandDirection
    head: HeadMovement


# 10가지 손 모양 × 위/아래/가로 × 고개 4방향. 검출불가는 출제하지 않는다.
CHALLENGE_TARGETS = tuple(
    ChallengeTarget(gesture, direction, head)
    for gesture in HandGesture
    for direction in (HandDirection.UP, HandDirection.DOWN, HandDirection.SIDEWAYS)
    for head in HeadMovement
)


def random_challenge(previous: ChallengeTarget | None = None) -> ChallengeTarget:
    """시작/다음 시도 때 한 번 뽑는다. 바로 전 시도와 완전히 같은 조합은 제외한다."""
    return secrets.choice(tuple(target for target in CHALLENGE_TARGETS if target != previous))


@dataclass(frozen=True)
class FrameReading:
    hands: list[GestureReading]
    pose: HeadPose | None


@dataclass(frozen=True)
class ChallengeResult:
    gesture: GestureHold | None
    head: HeadTurn | None

    @property
    def passed(self) -> bool:
        return all(part.passed for part in (self.gesture, self.head) if part is not None)


def sample_evenly[T](items: Sequence[T], limit: int = MAX_SAMPLED_FRAMES) -> list[T]:
    """라이브니스와 같이 영상 전체에서 최대 limit개를 고르게 뽑는다."""
    if len(items) <= limit:
        return list(items)
    return [items[index] for index in np.linspace(0, len(items) - 1, limit, dtype=int)]


def read_frame(frame: np.ndarray, landmarker: HandLandmarker, analyzer: LiveFaceAnalyzer) -> FrameReading:
    observation = analyzer.observe(frame)
    return FrameReading(
        hands=[read_gesture(hand.image_points) for hand in landmarker.detect(frame)],
        pose=pose_from(observation),
    )


def judge_challenge(
    frames: Sequence[FrameReading],
    *,
    gesture: HandGesture | None = None,
    direction: HandDirection = HandDirection.UP,
    head: HeadMovement | None = None,
) -> ChallengeResult:
    """시간순 프레임 판독값으로 지정된 항목만 판정한다."""
    return ChallengeResult(
        gesture=judge_hold([frame.hands for frame in frames], gesture, expected_direction=direction)
        if gesture
        else None,
        head=judge_head_turn([frame.pose for frame in frames], head) if head else None,
    )
