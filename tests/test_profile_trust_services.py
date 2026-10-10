from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from app.features.face_verification.liveness import LivenessEvaluation
from app.features.face_verification.matcher import FaceNotFound
from app.features.face_verification.schemas import (
    Challenge,
    LivenessResult,
    VerificationChallengeResponse,
    VerificationDecision,
)
from app.features.face_verification.schemas import (
    ReasonCode as VerificationReason,
)
from app.features.face_verification.service import FaceVerificationService
from app.features.primary_photo.analyzer import PhotoAnalysis
from app.features.primary_photo.schemas import PhotoDecision, Pose, Quality, ReasonCode
from app.features.primary_photo.service import PrimaryPhotoService


def quality() -> Quality:
    return Quality(
        width=800,
        height=800,
        face_count=1,
        detected_face_count=1,
        face_area_ratio=0.3,
        blur_variance=200,
        global_blur_variance=200,
        brightness=120,
        pose=Pose(yaw=1, pitch=2, roll=0),
    )


class FakeFaceAnalyzer:
    def __init__(self, reasons=None):
        self.reasons = reasons or []

    def analyze(self, _image):
        return PhotoAnalysis(quality=quality(), reasons=self.reasons)


class FakeSigner:
    def create(self):
        return VerificationChallengeResponse(
            challenge_token="token",
            challenges=[Challenge.LOOK_STRAIGHT],
            expires_at=datetime(2026, 10, 1, tzinfo=UTC),
        )

    def verify(self, token):
        assert token == "token"
        return [Challenge.LOOK_STRAIGHT]


class FakeLiveness:
    def __init__(self, passed=True):
        self.passed = passed

    def evaluate(self, _video, _suffix, _challenges):
        frame = np.zeros((20, 20, 3), dtype=np.uint8)
        return LivenessEvaluation(
            result=LivenessResult(
                passed=self.passed,
                completed_challenges=[Challenge.LOOK_STRAIGHT] if self.passed else [],
                sampled_frames=20,
                valid_face_frames=20,
                frontal_face_frames=20 if self.passed else 0,
            ),
            best_frame=frame if self.passed else None,
            candidate_frames=(frame, frame, frame) if self.passed else (),
        )


class FakeMatcher:
    def __init__(self, matched=True):
        self.matched = matched
        self.threshold = 0.42

    def compare(self, _reference, _live):
        return self.matched, 0.8 if self.matched else 0.1


class MissingLiveFeatureMatcher:
    threshold = 0.42

    def compare(self, _reference, _live):
        raise FaceNotFound("비교 특징을 만들 수 없습니다.")


def primary_service(*, quality_reasons=None):
    return PrimaryPhotoService(FakeFaceAnalyzer(quality_reasons))


def verification_service(*, live=True, matched=True):
    return FaceVerificationService(FakeMatcher(matched), FakeSigner(), FakeLiveness(live))


def test_primary_photo_passes_when_frontal_and_quality_checks_pass(monkeypatch):
    monkeypatch.setattr("app.features.primary_photo.service.decode_image", lambda _: np.zeros((20, 20, 3)))
    response = primary_service().inspect(b"image")
    assert response.decision is PhotoDecision.PASS
    assert response.reason_codes == []


def test_primary_photo_collects_frontal_and_quality_reasons(monkeypatch):
    monkeypatch.setattr("app.features.primary_photo.service.decode_image", lambda _: np.zeros((20, 20, 3)))
    response = primary_service(quality_reasons=[ReasonCode.NON_FRONTAL_FACE]).inspect(b"image")
    assert response.decision is PhotoDecision.RETRY
    assert response.reason_codes == [ReasonCode.NON_FRONTAL_FACE]


def test_verification_requires_liveness_before_face_match(monkeypatch):
    monkeypatch.setattr("app.features.face_verification.service.decode_image", lambda _: np.zeros((20, 20, 3)))
    response = verification_service(live=False).complete("token", b"photo", b"video", "video/mp4")
    assert response.decision is VerificationDecision.RETRY
    assert response.reason_codes == [VerificationReason.LIVENESS_FAILED]
    assert response.similarity is None


def test_verification_returns_match_result(monkeypatch):
    monkeypatch.setattr("app.features.face_verification.service.decode_image", lambda _: np.zeros((20, 20, 3)))
    verified = verification_service().complete("token", b"photo", b"video", "video/mp4")
    mismatch = verification_service(matched=False).complete("token", b"photo", b"video", "video/mp4")
    assert verified.decision is VerificationDecision.VERIFIED
    assert verified.match is not None
    assert verified.match.compared_frame_count == 3
    assert verified.match.frame_similarities == [0.8, 0.8, 0.8]
    assert verified.match.threshold == 0.42
    assert mismatch.decision is VerificationDecision.NOT_VERIFIED
    assert mismatch.reason_codes == [VerificationReason.FACE_MISMATCH]


def test_verification_does_not_report_no_face_after_liveness_found_faces(monkeypatch):
    monkeypatch.setattr("app.features.face_verification.service.decode_image", lambda _: np.zeros((20, 20, 3)))
    service = FaceVerificationService(MissingLiveFeatureMatcher(), FakeSigner(), FakeLiveness(True))

    response = service.complete("token", b"photo", b"video", "video/mp4")

    assert response.decision is VerificationDecision.RETRY
    assert response.reason_codes == ["FACE_COMPARISON_FAILED"]
