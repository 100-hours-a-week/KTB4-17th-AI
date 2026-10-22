from __future__ import annotations

from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from app.core.config import Settings
from app.features.face_verification.active_liveness import ActiveLivenessAnalyzer
from app.features.face_verification.challenge import CHALLENGE_TARGETS
from app.features.face_verification.continuous_hold import REQUIRED_HOLD_SECONDS
from app.features.face_verification.liveness import ChallengeSigner, InvalidChallenge
from app.features.face_verification.protocol import decode_target, encode_target
from app.features.face_verification.schemas import Challenge, ReasonCode, VerificationDecision
from app.features.face_verification.service import FaceVerificationService
from tests.test_hand_gesture import make_hand
from tests.test_live_face import centered_face


def test_signed_plan_is_unique_per_session_and_selects_each_attempt():
    signer = ChallengeSigner(
        Settings(liveness_token_secret="test-secret-" * 4, active_liveness_enabled=True, adaptive_liveness_enabled=True)
    )
    first, second = signer.create(), signer.create()
    assert first.protocol_version == 3 and len(first.challenge_plan) == 3
    assert first.challenge_token != second.challenge_token
    assert len(first.challenge_token) <= 2048
    for number, codes in enumerate(first.challenge_plan, 1):
        assert signer.verify(first.challenge_token, number) == codes
        assert decode_target(codes).head is None
        assert len(codes) == 3
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
    active = ChallengeSigner(Settings(_env_file=None, liveness_token_secret="s" * 32, adaptive_liveness_enabled=False))
    legacy = ChallengeSigner(Settings(_env_file=None, liveness_token_secret="s" * 32, active_liveness_enabled=False))
    old, new = legacy.create(), active.create()
    assert old.protocol_version == 1 and new.protocol_version == 2
    for attempt in range(1, 4):
        assert active.verify(old.challenge_token, attempt) == [Challenge.LOOK_STRAIGHT]
        assert legacy.verify(new.challenge_token, attempt) == new.challenges


def test_default_issuance_is_v2_plan_with_head_movement(monkeypatch):
    # BE는 protocol_version 1·2와 4개 코드 계획만 받는다. v3는 명시적으로 켤 때만 발급한다.
    monkeypatch.delenv("ACTIVE_LIVENESS_ENABLED", raising=False)
    monkeypatch.delenv("ADAPTIVE_LIVENESS_ENABLED", raising=False)
    issued = ChallengeSigner(Settings(_env_file=None, liveness_token_secret="s" * 32)).create()
    assert issued.protocol_version == 2 and len(issued.challenge_plan) == 3
    for codes in issued.challenge_plan:
        assert len(codes) == 4 and codes[0] == Challenge.LOOK_STRAIGHT
        assert codes[3] in (Challenge.LOOK_UP, Challenge.LOOK_DOWN)


def evaluate(
    monkeypatch,
    *,
    direction="DOWN",
    wrong_hand=False,
    no_hand=False,
    no_return=False,
    no_turn=False,
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
    adaptive=False,
    interrupted_pose=False,
    short_video=False,
    neutral_pitch=0,
    neutral_roll=0,
    leading_unready=False,
    brief_turn=False,
    turn_pitch=-20,
):
    target = next(
        item
        for item in CHALLENGE_TARGETS
        if item.gesture == "TWO" and item.direction == direction and item.head == "LOOK_UP"
    )
    points = make_hand("01000" if wrong_hand else "01100", rotate_deg=90 if direction == "SIDEWAYS" else 0)
    if direction == "DOWN" and not wrong_hand:
        points[:, 1] = 720 - points[:, 1]

    class Capture:
        def __init__(self, path):
            self.index = 0

        def get(self, prop):
            if prop == cv2.CAP_PROP_FPS:
                return 36 if short_video else 12
            return 10000 if oversized else (0 if no_metadata else (24 if underreported else 160))

        def isOpened(self):
            return self.index < 160

        def read(self):
            if self.index >= 160:
                return False, None
            frame = np.full((80, 80, 3), self.index, dtype=np.uint8)
            self.index += 1
            return True, frame

        def release(self):
            pass

    def face(frame):
        index = int(frame[0, 0, 0])
        if leading_unready and index < 45:
            return centered_face(face_count=0, pose_valid=False)
        if missing_middle and 12 <= index < 23:
            return centered_face(face_count=0)
        pitch = (
            turn_pitch
            if not adaptive
            and not no_turn
            and not missing_middle
            and 84 <= index < (88 if brief_turn else 160 if no_return else 130)
            else neutral_pitch
        )
        x = center_x + (0.018 if index % 2 else -0.018) if slight_center_drift else center_x
        return centered_face(
            pitch=pitch,
            roll=neutral_roll,
            center_x=0.05 if off_center else (0.65 if unstable_center and index == 1 else x),
            center_y=center_y,
            face_count=2 if multi_face and index == 27 else 1,
        )

    def hands(frame):
        if no_hand:
            return []
        if interrupted_pose and 50 <= int(frame[0, 0, 0]) < 135:
            return []
        box = np.array([25, 20, 55, 60]) if overlap else np.array([62, 5, 79, 60])
        return [SimpleNamespace(image_points=points, box=box)]

    monkeypatch.setattr("app.features.face_verification.active_liveness.cv2.VideoCapture", Capture)
    return ActiveLivenessAnalyzer(SimpleNamespace(observe=face), SimpleNamespace(detect=hands)).evaluate(
        b"video", ".webm", encode_target(target)[:3] if adaptive else encode_target(target)
    )


def test_basic_challenge_requires_frontal_then_continuous_pose_without_head_turn(monkeypatch):
    result = evaluate(monkeypatch, adaptive=True)
    assert result.result.passed
    assert result.result.frontal_hold_seconds >= REQUIRED_HOLD_SECONDS
    assert result.result.pose_hold_seconds >= REQUIRED_HOLD_SECONDS
    assert result.result.head_returned is None
    assert not evaluate(monkeypatch, adaptive=True, interrupted_pose=True).result.passed
    assert not evaluate(monkeypatch, adaptive=True, short_video=True).result.passed


@pytest.mark.parametrize("options", [{"neutral_pitch": 18}, {"neutral_roll": 22}, {"neutral_pitch": 24}])
def test_camera_frontal_pose_remains_valid_in_verification_session(monkeypatch, options):
    result = evaluate(monkeypatch, adaptive=True, **options)
    assert result.result.passed, result.result.reason_codes
    assert result.result.frontal_hold_seconds >= REQUIRED_HOLD_SECONDS
    assert result.result.pose_hold_seconds >= REQUIRED_HOLD_SECONDS


@pytest.mark.parametrize("options", [{"center_x": 0.62}, {"center_y": 0.65}])
def test_verification_allows_face_with_more_than_half_inside_guide(monkeypatch, options):
    result = evaluate(monkeypatch, adaptive=True, **options)
    assert result.result.passed, result.result.reason_codes


def test_only_server_risk_escalates_and_step_up_does_not_extend_expiry(monkeypatch):
    monkeypatch.setattr("app.features.face_verification.service.decode_image", lambda _: np.zeros((80, 80, 3)))
    signer = ChallengeSigner(
        Settings(liveness_token_secret="s" * 32, active_liveness_enabled=True, adaptive_liveness_enabled=True)
    )
    issued = signer.create()
    live = evaluate(monkeypatch, adaptive=True)
    matcher = SimpleNamespace(threshold=0.42, compare=lambda *args: (True, 0.46))
    service = FaceVerificationService(matcher, signer, SimpleNamespace(evaluate=lambda *args: live))
    result = service.complete(issued.challenge_token, b"photo", b"video", "video/webm")
    assert result.decision == VerificationDecision.RETRY
    assert result.reason_codes == [ReasonCode.ADDITIONAL_CHALLENGE_REQUIRED]
    assert result.next_challenge.challenges[:3] == issued.challenges
    assert len(result.next_challenge.challenges) == 4
    assert result.next_challenge.expires_at <= issued.expires_at
    assert signer.verify(result.next_challenge.challenge_token, 2) == result.next_challenge.challenges
    with pytest.raises(InvalidChallenge):
        signer.step_up(result.next_challenge.challenge_token)
    matcher.compare = lambda *args: (True, 0.9)
    assert (
        service.complete(issued.challenge_token, b"photo", b"video", "video/webm").decision
        == VerificationDecision.VERIFIED
    )
    live.result.repeated_frame_ratio = 0.9
    assert service.complete(issued.challenge_token, b"photo", b"video", "video/webm").next_challenge is not None
    matcher.compare = lambda *args: (False, 0.1)
    mismatch = service.complete(issued.challenge_token, b"photo", b"video", "video/webm")
    assert mismatch.decision == VerificationDecision.NOT_VERIFIED and mismatch.next_challenge is None


@pytest.mark.parametrize("direction", ["UP", "DOWN", "SIDEWAYS"])
@pytest.mark.parametrize("no_metadata", [False, True])
@pytest.mark.parametrize("no_return", [False, True])
def test_active_video_passes_with_requested_hand_and_head_without_requiring_return(
    monkeypatch, direction, no_metadata, no_return
):
    result = evaluate(monkeypatch, direction=direction, no_metadata=no_metadata, no_return=no_return)
    assert result.result.passed and result.result.expected_hand_direction == direction
    assert result.result.head_returned
    assert len(result.candidate_frames) >= 2
    assert all(frame[0, 0, 0] < 48 for frame in result.candidate_frames)
    assert result.result.head_hold_seconds >= REQUIRED_HOLD_SECONDS


@pytest.mark.parametrize(
    ("options", "reason"),
    [
        ({"wrong_hand": True}, ReasonCode.HAND_GESTURE_MISMATCH),
        ({"no_hand": True}, ReasonCode.HAND_NOT_DETECTED),
        ({"no_turn": True}, ReasonCode.HEAD_MOVEMENT_NOT_COMPLETED),
        ({"off_center": True}, ReasonCode.FACE_NOT_CENTERED),
        ({"multi_face": True}, ReasonCode.LIVENESS_FAILED),
        ({"missing_middle": True}, ReasonCode.HEAD_MOVEMENT_NOT_COMPLETED),
        ({"oversized": True}, ReasonCode.LIVENESS_FAILED),
        ({"overlap": True}, ReasonCode.FACE_COMPARISON_FAILED),
        ({"underreported": True}, ReasonCode.LIVENESS_FAILED),
    ],
)
def test_wrong_or_incomplete_active_challenge_never_produces_match_frames(monkeypatch, options, reason):
    result = evaluate(monkeypatch, **options)
    assert not result.result.passed
    assert reason in result.result.reason_codes
    assert result.best_frame is None and result.candidate_frames == ()


def test_liveness_failure_logs_failed_condition_counts(monkeypatch, caplog):
    caplog.set_level("WARNING", logger="app.features.face_verification.active_liveness")
    result = evaluate(monkeypatch, multi_face=True)
    assert ReasonCode.LIVENESS_FAILED in result.result.reason_codes
    message = caplog.records[-1].getMessage()
    assert "reasons=LIVENESS_FAILED" in message and "multi_face_frames=" in message
    assert "multi_face_frames=0 " not in message


def test_head_stage_requires_same_angle_as_browser(monkeypatch):
    # 서버도 브라우저와 같은 12°를 요구한다.
    assert evaluate(monkeypatch, turn_pitch=-12.5).result.passed
    failed = evaluate(monkeypatch, turn_pitch=-10.5)
    assert ReasonCode.HEAD_MOVEMENT_NOT_COMPLETED in failed.result.reason_codes


def test_head_failure_log_traces_each_head_frame(monkeypatch, caplog):
    caplog.set_level("WARNING", logger="app.features.face_verification.active_liveness")
    evaluate(monkeypatch, turn_pitch=-9)
    message = caplog.records[-1].getMessage()
    assert "head_trace=base=0.0:" in message
    assert "d9" in message


def test_identity_uses_initial_frontal_frames_not_return_frames(monkeypatch):
    monkeypatch.setattr("app.features.face_verification.service.decode_image", lambda _: np.zeros((80, 80, 3)))
    live = evaluate(monkeypatch)
    matcher = SimpleNamespace(
        threshold=0.42, compare=lambda reference, frame: (True, 0.9 if frame[0, 0, 0] < 48 else 0.1)
    )
    service = FaceVerificationService(
        matcher, SimpleNamespace(verify=lambda *args: []), SimpleNamespace(evaluate=lambda *args: live)
    )
    response = service.complete("token", b"photo", b"video", "video/webm")
    assert response.decision == VerificationDecision.VERIFIED
    assert response.match.aggregation == "median_initial_frontal"


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


@pytest.mark.parametrize("score", [0.1, 0.41])
def test_initial_identity_mismatch_is_rejected(monkeypatch, score):
    monkeypatch.setattr("app.features.face_verification.service.decode_image", lambda _: np.zeros((80, 80, 3)))
    live = evaluate(monkeypatch)
    service = FaceVerificationService(
        SimpleNamespace(threshold=0.42, compare=lambda *args: (False, score)),
        SimpleNamespace(verify=lambda *args: []),
        SimpleNamespace(evaluate=lambda *args: live),
    )
    response = service.complete("token", b"photo", b"video", "video/webm")
    assert response.decision == VerificationDecision.NOT_VERIFIED
    assert response.reason_codes == [ReasonCode.FACE_MISMATCH]


def test_one_failed_comparison_still_verifies_with_two_valid_candidates(monkeypatch):
    from app.features.face_verification.matcher import FaceNotFound

    monkeypatch.setattr("app.features.face_verification.service.decode_image", lambda _: np.zeros((80, 80, 3)))
    live = evaluate(monkeypatch)

    first = live.candidate_frames[0]

    def compare(reference, frame):
        if frame is first:
            raise FaceNotFound("missing")
        return True, 0.9

    service = FaceVerificationService(
        SimpleNamespace(threshold=0.42, compare=compare),
        SimpleNamespace(verify=lambda *args: []),
        SimpleNamespace(evaluate=lambda *args: live),
    )
    response = service.complete("token", b"photo", b"video", "video/webm")
    assert response.decision == VerificationDecision.VERIFIED
    assert response.match.compared_frame_count == 2


@pytest.mark.parametrize("options", [{"center_x": 0.61, "center_y": 0.60}, {"slight_center_drift": True}])
def test_active_video_accepts_small_offset_and_natural_position_jitter(monkeypatch, options):
    result = evaluate(monkeypatch, **options)
    assert result.result.passed
    assert len(result.candidate_frames) >= 2


def test_active_liveness_skips_unsampled_frames_with_grab_and_downscales_hd_frames(monkeypatch):
    target = next(
        item
        for item in CHALLENGE_TARGETS
        if item.gesture == "TWO" and item.direction == "DOWN" and item.head == "LOOK_UP"
    )
    points = make_hand("01100")
    points[:, 1] = 720 - points[:, 1]
    stats = {"grab": 0, "read": 0, "observed_shapes": []}

    class GrabCapture:
        def __init__(self, _path):
            self.index = 0

        def get(self, prop):
            if prop == cv2.CAP_PROP_FPS:
                return 15
            if prop == cv2.CAP_PROP_FRAME_COUNT:
                return 0  # WebM without frame count header
            return self.index * (1000.0 / 15.0)

        def isOpened(self):
            return self.index < 96

        def grab(self):
            if self.index >= 96:
                return False
            stats["grab"] += 1
            self.index += 1
            return True

        def read(self):
            if self.index >= 96:
                return False, None
            stats["read"] += 1
            frame = np.full((1080, 1920, 3), self.index, dtype=np.uint8)
            self.index += 1
            return True, frame

        def release(self):
            pass

    def observe(frame):
        stats["observed_shapes"].append(frame.shape[:2])
        return centered_face()

    def detect(_frame):
        return [SimpleNamespace(image_points=points, box=np.array([62, 5, 79, 60]))]

    monkeypatch.setattr("app.features.face_verification.active_liveness.cv2.VideoCapture", GrabCapture)
    evaluation = ActiveLivenessAnalyzer(SimpleNamespace(observe=observe), SimpleNamespace(detect=detect)).evaluate(
        b"video", ".webm", encode_target(target)[:3]
    )

    assert evaluation.result.passed
    # 헤더 없는 영상도 한 번만 순회하며 분석 프레임만 읽는다.
    assert stats["read"] <= 48
    assert stats["grab"] + stats["read"] == 96
    assert all(max(shape) == 720 for shape in stats["observed_shapes"])


def test_initial_preparation_does_not_fail_an_otherwise_valid_sequence(monkeypatch):
    result = evaluate(monkeypatch, adaptive=True, leading_unready=True)
    assert result.result.passed
    assert result.result.frontal_hold_seconds >= REQUIRED_HOLD_SECONDS
    assert result.result.pose_hold_seconds >= REQUIRED_HOLD_SECONDS


def test_brief_head_motion_is_not_a_three_second_head_hold(monkeypatch):
    result = evaluate(monkeypatch, brief_turn=True)
    assert not result.result.passed
    assert ReasonCode.HEAD_MOVEMENT_NOT_COMPLETED in result.result.reason_codes
    assert result.result.head_hold_seconds < 3


def test_two_failed_identity_candidates_still_require_retry(monkeypatch):
    from app.features.face_verification.matcher import FaceNotFound

    live = evaluate(monkeypatch, adaptive=True)
    remaining = iter([False, False, True])
    monkeypatch.setattr("app.features.face_verification.service.decode_image", lambda _: np.zeros((80, 80, 3)))

    def compare(*args):
        if not next(remaining):
            raise FaceNotFound("missing")
        return True, 0.9

    service = FaceVerificationService(
        SimpleNamespace(threshold=0.42, compare=compare),
        SimpleNamespace(verify=lambda *args: []),
        SimpleNamespace(evaluate=lambda *args: live),
    )
    response = service.complete("token", b"photo", b"video", "video/webm")
    assert response.decision == VerificationDecision.RETRY
    assert response.reason_codes == [ReasonCode.FACE_COMPARISON_FAILED]


def test_only_three_candidate_pixel_copies_are_retained(monkeypatch):
    import weakref

    from app.features.face_verification import active_liveness

    stats = {"alive": 0, "peak": 0}

    def release():
        stats["alive"] -= 1

    class Frame(np.ndarray):
        def copy(self, *args, **kwargs):
            result = super().copy(*args, **kwargs)
            stats["alive"] += 1
            stats["peak"] = max(stats["peak"], stats["alive"])
            weakref.finalize(result, release)
            return result

    monkeypatch.setattr(
        active_liveness,
        "_downscale_frame",
        lambda frame: np.full((405, 720, 3), frame[0, 0, 0], dtype=np.uint8).view(Frame),
    )
    result = evaluate(monkeypatch, adaptive=True)
    assert result.result.passed
    assert len(result.candidate_frames) == 3
    assert stats["peak"] <= 3
    assert sum(frame.nbytes for frame in result.candidate_frames) == 2_624_400
