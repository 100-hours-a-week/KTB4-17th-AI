"""Reuse the existing semantic-release workflow; never calculate or issue a version."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

SOURCE_REPOSITORY = "100-hours-a-week/KTB4-17th-AI"
SEMVER_TAG = r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"


def validate_image_tag(tag: str, source_sha: str, branch: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha) or branch not in ("dev", "main"):
        raise ValueError("a full source SHA and original dev/main branch are required")
    if branch == "dev":
        valid = tag == f"dev-{source_sha}"
    else:
        valid = tag == f"sha-{source_sha}" or re.fullmatch(SEMVER_TAG, tag)
    if not valid:
        raise ValueError("image tag must be the branch-specific full SHA or verified production release tag")


def resolve_version(
    source,
    source_sha: str,
    *,
    branch: str,
    timeout: int = 600,
    poll_interval: int = 5,
    clock=time.monotonic,
    sleep=time.sleep,
) -> dict:
    validate_image_tag(f"dev-{source_sha}" if branch == "dev" else f"sha-{source_sha}", source_sha, branch)
    if source.repository != SOURCE_REPOSITORY:
        raise ValueError("release metadata must come from the original AI repository")
    if not 0 < timeout <= 1800 or not 0 < poll_interval <= 30:
        raise ValueError("release wait must be bounded to 1..1800 seconds with a 1..30 second poll interval")
    result = {"source_sha": source_sha, "source_branch": branch, "release_tag": None}
    if branch == "dev":
        return {**result, "image_tag": f"dev-{source_sha}", "release_status": "development"}
    deadline = clock() + timeout
    query = urllib.parse.urlencode({"branch": "main", "event": "push", "head_sha": source_sha, "per_page": 100})
    while True:
        response = source.request("GET", f"actions/workflows/release.yml/runs?{query}")
        runs = [
            run
            for run in response["workflow_runs"]
            if run.get("head_sha") == source_sha
            and run.get("head_branch") == "main"
            and run.get("event") == "push"
            and run.get("path", "").split("@")[0] == ".github/workflows/release.yml"
            and run.get("repository", {}).get("full_name") == SOURCE_REPOSITORY
            and run.get("head_repository", {}).get("full_name") == SOURCE_REPOSITORY
        ]
        if runs:
            latest = max(runs, key=lambda run: (run["run_number"], run.get("run_attempt", 1), run["id"]))
            if latest["status"] == "completed":
                if latest.get("conclusion") != "success":
                    raise RuntimeError(f"release workflow {latest['id']} failed: {latest.get('conclusion')}")
                break
        remaining = deadline - clock()
        if remaining <= 0:
            raise RuntimeError("timed out waiting for this source SHA's release workflow")
        sleep(min(poll_interval, remaining))

    matches = []
    references = source.request("GET", "git/matching-refs/tags/v")
    if not isinstance(references, list) or len(references) > 1000:
        raise RuntimeError("unexpected or excessive release tag response")
    for reference in references:
        tag = reference["ref"].removeprefix("refs/tags/")
        if not re.fullmatch(SEMVER_TAG, tag):
            continue
        target = reference["object"]
        seen = set()
        while target.get("type") == "tag":
            if target["sha"] in seen or len(seen) >= 8 or clock() > deadline:
                raise RuntimeError("invalid, cyclic or timed out annotated release tag resolution")
            seen.add(target["sha"])
            if not re.fullmatch(r"[0-9a-f]{40}", target["sha"]):
                raise RuntimeError("invalid annotated tag object SHA")
            target = source.request("GET", f"git/tags/{target['sha']}")["object"]
        if target.get("type") != "commit":
            raise RuntimeError("release tag must resolve to a commit")
        if target["sha"] == source_sha:
            matches.append(tag)
    if len(matches) > 1:
        raise RuntimeError("multiple semantic release tags point to this source; refusing ambiguous version")
    tag = matches[0] if matches else f"sha-{source_sha}"
    return {
        **result,
        "image_tag": tag,
        "release_tag": matches[0] if matches else None,
        "release_status": "released" if matches else "no-release",
        "workflow_run_id": latest["id"],
        "workflow_run_attempt": latest.get("run_attempt", 1),
        "workflow_run_url": latest["html_url"],
    }


def main() -> int:
    # Imported only by the CLI; resolve_version itself has no client dependency.
    from scripts.jenkins.gitops_release import GitHub

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path(".ci-reports"))
    args = parser.parse_args()
    if os.environ.get("CHANGE_ID") or os.environ.get("CHANGE_FORK"):
        raise ValueError("PR/fork builds cannot resolve deployment versions")
    result = resolve_version(
        GitHub(os.environ.get("SOURCE_GITHUB_TOKEN", ""), SOURCE_REPOSITORY),
        args.source_sha,
        branch=os.environ.get("BRANCH_NAME", ""),
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "release-version.json").write_text(json.dumps(result, indent=2) + "\n")
    print(result["image_tag"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError) as error:
        print(f"Release version resolution failed: {error}", file=sys.stderr)
        sys.exit(1)
