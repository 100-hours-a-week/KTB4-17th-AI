"""Host Python 3.9+ check: a new tag needs ECR's atomic overwrite protection."""

import hashlib
import json
import re
import sys
from pathlib import Path


def is_immutable(repository: dict, tag: str) -> bool:
    mode = repository.get("imageTagMutability")
    matches = False
    for item in repository.get("imageTagMutabilityExclusionFilters") or []:
        pattern = item.get("filter", "")
        if item.get("filterType") != "WILDCARD" or not re.fullmatch(r"[A-Za-z0-9_.*-]+", pattern):
            raise ValueError("unknown ECR tag exclusion filter")
        matches = matches or bool(re.fullmatch(re.escape(pattern).replace(r"\*", ".*"), tag))
    if mode == "IMMUTABLE":
        return True
    if mode == "MUTABLE":
        return False
    if mode == "IMMUTABLE_WITH_EXCLUSION":
        return not matches
    if mode == "MUTABLE_WITH_EXCLUSION":
        return matches
    raise ValueError("unknown ECR tag mutability mode")


def extract_manifest(response: dict, digest: str) -> bytes:
    images = response.get("images", [])
    if response.get("failures") or len(images) != 1 or images[0].get("imageId", {}).get("imageDigest") != digest:
        raise ValueError("ECR did not return exactly the requested image manifest")
    raw = images[0]["imageManifest"].encode("utf-8")
    if "sha256:" + hashlib.sha256(raw).hexdigest() != digest or json.loads(raw).get("schemaVersion") != 2:
        raise ValueError("ECR manifest bytes do not match the verified digest")
    return raw


if __name__ == "__main__":
    if sys.argv[1] == "manifest":
        with open(sys.argv[2]) as source:
            raw = extract_manifest(json.load(source), sys.argv[3])
        Path(sys.argv[4]).write_bytes(raw)
    else:
        with open(sys.argv[1]) as source:
            repository = json.load(source)["repositories"][0]
        if not is_immutable(repository, sys.argv[2]):
            print(
                "New version tag requires ECR immutability for that tag; refusing a mutable check/put race.",
                file=sys.stderr,
            )
            sys.exit(1)
