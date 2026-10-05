from __future__ import annotations

import io
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.features.face_verification import api as verification_api
from app.features.face_verification.schemas import Challenge, VerificationChallengeResponse
from app.features.primary_photo import api as primary_api
from app.features.primary_photo.schemas import PhotoDecision, PrimaryPhotoResponse, Quality
from app.features.synthetic_detection import api as synthetic_api
from app.features.synthetic_detection.schemas import SyntheticDecision, SyntheticResult


def image_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(output, format="JPEG")
    return output.getvalue()


def synthetic_result() -> SyntheticResult:
    return SyntheticResult(
        decision=SyntheticDecision.CLEAR,
        probability=0.1,
        provenance="none",
        model_version="fake",
    )


class FakePrimaryService:
    def inspect(self, _data):
        return PrimaryPhotoResponse(
            decision=PhotoDecision.PASS,
            reason_codes=[],
            quality=Quality(
                width=32,
                height=32,
                face_count=1,
                detected_face_count=1,
                face_area_ratio=0.3,
                blur_variance=100,
                global_blur_variance=100,
                brightness=120,
            ),
        )


class FakeSyntheticService:
    def inspect(self, _data, _content_type):
        return synthetic_result()


class FakeVerificationService:
    def create_challenge(self):
        return VerificationChallengeResponse(
            challenge_token="token",
            challenges=[Challenge.LOOK_STRAIGHT],
            expires_at=datetime(2026, 10, 1, tzinfo=UTC),
        )


def client() -> TestClient:
    app = FastAPI()
    app.include_router(primary_api.router)
    app.include_router(synthetic_api.router)
    app.include_router(verification_api.router)
    app.dependency_overrides[primary_api.get_primary_photo_service] = FakePrimaryService
    app.dependency_overrides[synthetic_api.get_synthetic_service] = FakeSyntheticService
    app.dependency_overrides[verification_api.get_face_verification_service] = FakeVerificationService
    return TestClient(app)


def test_primary_photo_endpoint():
    response = client().post(
        "/v1/profile-trust/photos/primary/frontal-check",
        files={"image": ("photo.jpg", image_bytes(), "image/jpeg")},
    )
    assert response.status_code == 200
    assert response.json()["decision"] == "PASS"


def test_primary_synthetic_photo_rejects_unsupported_type():
    response = client().post(
        "/v1/profile-trust/photos/primary/synthetic-check",
        files={"image": ("photo.txt", b"not an image", "text/plain")},
    )
    assert response.status_code == 415


@pytest.mark.parametrize(
    "path",
    [
        "/v1/profile-trust/photos/primary/frontal-check",
        "/v1/profile-trust/photos/primary/synthetic-check",
    ],
)
def test_photo_endpoints_reject_webp(path):
    response = client().post(
        path,
        files={"image": ("photo.webp", b"not accepted", "image/webp")},
    )

    assert response.status_code == 415
    assert response.json() == {"detail": "JPEG/JPG, PNG 이미지만 지원합니다."}


def test_primary_synthetic_photo_endpoint():
    response = client().post(
        "/v1/profile-trust/photos/primary/synthetic-check",
        files={"image": ("photo.jpg", image_bytes(), "image/jpeg")},
    )
    assert response.status_code == 200
    assert response.json()["decision"] == "CLEAR"


def test_verification_challenge_endpoint():
    response = client().post("/v1/profile-trust/verifications/challenges")
    assert response.status_code == 201
    assert response.json()["challenges"] == ["LOOK_STRAIGHT"]
