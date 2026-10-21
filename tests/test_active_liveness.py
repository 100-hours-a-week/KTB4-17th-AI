from __future__ import annotations

from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from app.core.config import Settings
from app.features.face_verification.active_liveness import ActiveLivenessAnalyzer
from app.features.face_verification.challenge import CHALLENGE_TARGETS
from app.features.face_verification.liveness import ChallengeSigner, InvalidChallenge
from app.features.face_verification.protocol import decode_target, encode_target
from app.features.face_verification.schemas import Challenge, ReasonCode, VerificationDecision
from app.features.face_verification.service import FaceVerificationService
from tests.test_hand_gesture import make_hand
from tests.test_live_face import centered_face


def test_signed_plan_is_unique_per_session_and_selects_each_attempt(monkeypatch):
    monkeypatch.delenv("ACTIVE_LIVENESS_ENABLED", raising=False)
    signer = ChallengeSigner(Settings(_env_file=None, liveness_token_secret="test-secret-" * 4))
    first, second = signer.create(), signer.create()
    assert first.protocol_version == 2 and len(first.challenge_plan) == 3
    assert first.challenge_token != second.challenge_token
    assert len(first.challenge_token) <= 2048
    for number, codes in enumerate(first.challenge_plan, 1):
        assert signer.verify(first.challenge_token, number) == codes
        assert decode_target(codes) in CHALLENGE_TARGETS
    assert all(codes == first.challenges for codes in first.challenge_plan)


@pytest.mark.parametrize("attempt", [0, -1, 4, 5, True, 1.2, "1"])
def test_signed_plan_rejects_invalid_attempt_number(attempt):
    signer = ChallengeSigner(Settings(liveness_token_secret="s" * 32, active_liveness_enabled=True))
    with pytest.raises(InvalidChallenge):
        signer.verify(signer.create().challenge_token, attempt)


def test_legacy_switch_issues_only_expiring_fixed_pose_challenges():
    signer = ChallengeSigner(Settings(active_liveness_enabled=False))
    issued = signer.create()
    assert issued.protocol_version == 1 and issued.challenge_plan is None
    assert signer.verify(issued.challenge_token, 3) == [Challenge.LOOK_STRAIGHT]


def test_switching_issuance_preserves_existing_tokens_in_both_directions(monkeypatch):
    monkeypatch.delenv("ACTIVE_LIVENESS_ENABLED", raising=False)
    active = ChallengeSigner(Settings(_env_file=None, liveness_token_secret="s" * 32))
    legacy = ChallengeSigner(Settings(_env_file=None, liveness_token_secret="s" * 32, active_liveness_enabled=False))
    old, new = legacy.create(), active.create()
    assert old.protocol_version == 1 and new.protocol_version == 2
    for attempt in range(1, 4):
        assert active.verify(old.challenge_token, attempt) == [Challenge.LOOK_STRAIGHT]
        assert legacy.verify(new.challenge_token, attempt) == new.challenges


def evaluate(
    monkeypatch,
    *,
    direction="DOWN",
    wrong_hand=False,
    no_hand=False,
    no_return=False,
    off_center=False,
    multi_face=False,
    missing_middle=False,
    no_metadata=False,
    oversized=False,
    overlap=False,
    underreported=False,
    unstable_center=False,
    center_x=0.5,
    center_y=0.48,
    slight_center_drift=False,
):
    target = next(
        item
        for item in CHALLENGE_TARGETS
        if item.gesture == "TWO" and item.direction == direction and item.head == "LOOK_UP"
    )
    points = make_hand("01100", rotate_deg=90 if direction == "SIDEWAYS" and not wrong_hand else 0)
    if direction == "DOWN" and not wrong_hand:
        points[:, 1] = 720 - points[:, 1]

    class Capture:
        def __init__(self, path):
            self.index = 0

        def get(self, prop):
            if prop == cv2.CAP_PROP_FPS:
                return 7
            return 10000 if oversized else (0 if no_metadata else (24 if underreported else 48))

        def isOpened(self):
            return self.index < 48

        def read(self):
            if self.index >= 48:
                return False, None
            frame = np.full((80, 80, 3), self.index, dtype=np.uint8)
            self.index += 1
            return True, frame

        def release(self):
            pass

    def face(frame):
        index = int(frame[0, 0, 0])
        if missing_middle and 25 <= index < 36:
            return centered_face(face_count=0)
        pitch = -20 if 12 <= index < (48 if no_return else 30) else 0
        x = center_x + (0.018 if index % 2 else -0.018) if slight_center_drift else center_x
        return centered_face(
            pitch=pitch,
            center_x=0.2 if off_center and index < 3 else (0.55 if unstable_center and index == 1 else x),
            center_y=center_y,
            face_count=2 if multi_face and index == 25 else 1,
        )

    def hands(frame):
        if no_hand:
            return []
        box = np.array([25, 20, 55, 60]) if overlap else np.array([62, 5, 79, 60])
        return [SimpleNamespace(image_points=points, box=box)]

    monkeypatch.setattr("app.features.face_verification.active_liveness.cv2.VideoCapture", Capture)
    return ActiveLivenessAnalyzer(SimpleNamespace(observe=face), SimpleNamespace(detect=hands)).evaluate(
        b"video", ".webm", encode_target(target)
    )


@pytest.mark.parametrize("direction", ["UP", "DOWN", "SIDEWAYS"])
@pytest.mark.parametrize("no_metadata", [False, True])
def test_active_video_passes_only_with_requested_hand_head_and_return(monkeypatch, direction, no_metadata):
    result = evaluate(monkeypatch, direction=direction, no_metadata=no_metadata)
    assert result.result.passed and result.result.expected_hand_direction == direction
    assert result.result.head_returned
    assert len(result.candidate_frames) == 2
    assert result.candidate_frames[0][0, 0, 0] == 0
    assert result.candidate_frames[1][0, 0, 0] == 47


@pytest.mark.parametrize("options", [{"center_x": 0.61, "center_y": 0.60}, {"slight_center_drift": True}])
def test_active_video_accepts_small_offset_and_natural_position_jitter(monkeypatch, options):
    result = evaluate(monkeypatch, **options)
    assert result.result.passed
    assert len(result.candidate_frames) == 2


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"wrong_hand": True}, ReasonCode.HAND_GESTURE_MISMATCH),
        ({"no_hand": True}, ReasonCode.HAND_NOT_DETECTED),
        ({"no_return": True}, ReasonCode.HEAD_MOVEMENT_NOT_COMPLETED),
        ({"off_center": True}, ReasonCode.FACE_NOT_CENTERED),
        ({"multi_face": True}, ReasonCode.LIVENESS_FAILED),
        ({"missing_middle": True}, ReasonCode.HEAD_MOVEMENT_NOT_COMPLETED),
        ({"oversized": True}, ReasonCode.LIVENESS_FAILED),
        ({"overlap": True}, ReasonCode.FACE_COMPARISON_FAILED),
        ({"underreported": True}, ReasonCode.LIVENESS_FAILED),
        ({"unstable_center": True}, ReasonCode.FACE_NOT_CENTERED),
    ],
)
def test_wrong_or_incomplete_active_challenge_never_produces_match_frames(monkeypatch, options, reason):
    result = evaluate(monkeypatch, **options)
    assert not result.result.passed
    assert reason in result.result.reason_codes
    assert result.best_frame is None and result.candidate_frames == ()


def test_identity_must_match_at_both_start_and_end(monkeypatch):
    monkeypatch.setattr("app.features.face_verification.service.decode_image", lambda _: np.zeros((80, 80, 3)))
    live = evaluate(monkeypatch)
    matcher = SimpleNamespace(
        threshold=0.42, compare=lambda reference, frame: (True, 0.9 if frame[0, 0, 0] == 0 else 0.1)
    )
    service = FaceVerificationService(
        matcher, SimpleNamespace(verify=lambda *args: []), SimpleNamespace(evaluate=lambda *args: live)
    )
    response = service.complete("token", b"photo", b"video", "video/webm")
    assert response.decision == VerificationDecision.NOT_VERIFIED
    assert response.match.aggregation == "minimum_start_end"


def test_liveness_failure_prevents_identity_match(monkeypatch):
    monkeypatch.setattr("app.features.face_verification.service.decode_image", lambda _: np.zeros((80, 80, 3)))
    live = evaluate(monkeypatch, wrong_hand=True)

    def forbidden(*args):
        pytest.fail("Face identity comparison ran before the active challenge passed")

    service = FaceVerificationService(
        SimpleNamespace(compare=forbidden),
        SimpleNamespace(verify=lambda *args: []),
        SimpleNamespace(evaluate=lambda *args: live),
    )
    response = service.complete("token", b"photo", b"video", "video/webm")
    assert response.decision == VerificationDecision.RETRY
    assert ReasonCode.HAND_GESTURE_MISMATCH in response.reason_codes
