"""시도별 고개·손 모양·위아래를 동일한 코드로 전달하는 서비스 간 계약."""

from .challenge import ChallengeTarget
from .gesture import HandDirection, HandGesture
from .head import HeadMovement
from .schemas import Challenge

MAX_ATTEMPTS = 3


def encode_target(target: ChallengeTarget) -> list[Challenge]:
    codes = [
        Challenge.LOOK_STRAIGHT,
        Challenge(f"HAND_{target.gesture.value}"),
        Challenge(f"HAND_{target.direction.value}"),
    ]
    return codes + [Challenge(target.head.value)] if target.head else codes


def decode_target(challenges: list[Challenge]) -> ChallengeTarget:
    if len(challenges) not in (3, 4) or challenges[0] != Challenge.LOOK_STRAIGHT:
        raise ValueError("챌린지 형식이 올바르지 않습니다.")
    gesture = HandGesture(challenges[1].removeprefix("HAND_"))
    direction = HandDirection(challenges[2].removeprefix("HAND_"))
    if direction not in (HandDirection.UP, HandDirection.DOWN, HandDirection.SIDEWAYS):
        raise ValueError("손 방향이 올바르지 않습니다.")
    return ChallengeTarget(gesture, direction, HeadMovement(challenges[3]) if len(challenges) == 4 else None)
