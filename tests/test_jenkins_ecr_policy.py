from __future__ import annotations

import hashlib
import importlib

import pytest


@pytest.mark.parametrize(
    "mode,pattern,tag,expected",
    [
        ("IMMUTABLE", None, "v3.1.0", True),
        ("MUTABLE", None, "v3.1.0", False),
        ("IMMUTABLE_WITH_EXCLUSION", "latest", "v3.1.0", True),
        ("IMMUTABLE_WITH_EXCLUSION", "dev-*", "dev-abc", False),
        ("MUTABLE_WITH_EXCLUSION", "dev-*", "dev-abc", True),
        ("MUTABLE_WITH_EXCLUSION", "v*", "sha-abc", False),
    ],
)
def test_only_immutable_tag_scope_permits_atomic_new_version(mode, pattern, tag, expected):
    module = importlib.import_module("scripts.jenkins.ecr_tag_policy")
    repository = {
        "imageTagMutability": mode,
        "imageTagMutabilityExclusionFilters": [{"filterType": "WILDCARD", "filter": pattern}] if pattern else [],
    }
    assert module.is_immutable(repository, tag) is expected


@pytest.mark.parametrize("raw", [b'{"schemaVersion":2}', b'{"schemaVersion":2}\n', b'{"schemaVersion":2}\r\n'])
def test_manifest_json_extraction_preserves_the_exact_digest_addressed_bytes(raw):
    module = importlib.import_module("scripts.jenkins.ecr_tag_policy")
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    response = {"images": [{"imageId": {"imageDigest": digest}, "imageManifest": raw.decode()}], "failures": []}
    assert module.extract_manifest(response, digest) == raw


def test_manifest_hash_mismatch_or_ecr_partial_failure_is_rejected():
    module = importlib.import_module("scripts.jenkins.ecr_tag_policy")
    for response in [
        {"images": [], "failures": [{"failureCode": "ImageNotFound"}]},
        {"images": [{"imageId": {"imageDigest": "sha256:" + "a" * 64}, "imageManifest": '{"schemaVersion":2}'}]},
    ]:
        with pytest.raises(ValueError):
            module.extract_manifest(response, "sha256:" + "a" * 64)
