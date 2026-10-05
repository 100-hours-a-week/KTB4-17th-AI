from __future__ import annotations

import json

from app.main import app
from scripts.export_profile_trust_openapi import OUTPUT, profile_trust_openapi

EXPECTED_OPERATIONS = {
    "/ai/api/v1/profile-trust/photos/primary/frontal-check": "checkPrimaryPhotoFrontal",
    "/ai/api/v1/profile-trust/photos/primary/synthetic-check": "checkPrimaryPhotoSynthetic",
    "/ai/api/v1/profile-trust/verifications/challenges": "createVerificationChallenge",
    "/ai/api/v1/profile-trust/verifications/complete": "completeFaceVerification",
}


def test_backend_operation_ids_are_stable():
    schema = app.openapi()

    for path, operation_id in EXPECTED_OPERATIONS.items():
        assert schema["paths"][path]["post"]["operationId"] == operation_id


def test_exported_openapi_matches_running_application():
    exported = json.loads(OUTPUT.read_text(encoding="utf-8"))

    assert exported == json.loads(json.dumps(profile_trust_openapi()))
