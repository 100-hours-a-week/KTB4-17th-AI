"""Prepare an AI GitOps patch; create a CLOUD PR only with --create-pr on main."""

from __future__ import annotations

import argparse
import base64
import difflib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

GITOPS_REPOSITORY = "100-hours-a-week/KTB4-17th-CLOUD"
IMAGE_REPOSITORY = "975779326240.dkr.ecr.ap-northeast-2.amazonaws.com/star23/ai"
VALUES_PATHS = tuple(
    f"manifest/helm/star23-service/values/{role}.yaml"
    for role in (
        "onboarding-persona-ai",
        "onboarding-face-certify-ai",
        "chat-ai-practice",
        "chat-ai-simulation",
        "chat-ai-cleanup",
    )
)


def validate_revision(source_sha: str, digest: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("source SHA must be the full checkout GIT_COMMIT")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("image digest must be sha256 followed by 64 lowercase hex characters")


def plan_changes(files: dict[str, str], digest: str) -> dict[str, str]:
    """Change only direct image.digest scalars in the known CLOUD values layout.

    Reject an unfamiliar layout instead of reserializing YAML or touching other
    settings. This preserves comments, secret references and unrelated values.
    """
    validate_revision("0" * 40, digest)
    if set(files) != set(VALUES_PATHS):
        raise ValueError("all five AI values files are required")
    changes = {}
    for path, original in files.items():
        lines = original.splitlines(keepends=True)
        # This renderer deliberately accepts only plain top-level mapping keys.
        # Quoted/escaped/complex keys could hide a second effective image key.
        top_level = [line for line in lines if line.strip() and not line.startswith((" ", "\t", "#"))]
        if any(not re.match(r"^[A-Za-z_][A-Za-z0-9_-]*:", line) for line in top_level):
            raise ValueError(f"unsupported top-level YAML layout in {path}")
        starts = [index for index, line in enumerate(lines) if line.startswith("image:")]
        if len(starts) != 1 or lines[starts[0]].rstrip() != "image:":
            raise ValueError(f"expected one plain image mapping in {path}")
        start = starts[0] + 1
        end = next(
            (i for i in range(start, len(lines)) if lines[i].strip() and not lines[i].startswith((" ", "#"))),
            len(lines),
        )
        block = "".join(lines[start:end])
        fields = [line for line in lines[start:end] if line.strip() and not line.lstrip().startswith("#")]
        keys = [re.match(r"^  ([A-Za-z_][A-Za-z0-9_-]*):", line) for line in fields]
        if any(key is None for key in keys) or len({key[1] for key in keys}) != len(keys):
            raise ValueError(f"duplicate or unsupported image fields in {path}")
        repositories = re.findall(r"^  repository: *['\"]?([^'\"\s]+)['\"]? *(?:#.*)?$", block, re.MULTILINE)
        if repositories != [IMAGE_REPOSITORY]:
            raise ValueError(f"unexpected image repository in {path}")
        pattern = r"^(  digest: *)(['\"]?)(sha256:[0-9a-f]{64})\2([ \t]*(?:#.*)?\r?)$"
        matches = list(re.finditer(pattern, block, re.MULTILINE))
        if len(matches) != 1 or len(re.findall(r"^  digest:", block, re.MULTILINE)) != 1:
            raise ValueError(f"expected one sha256 image.digest in {path}")
        changed = re.sub(pattern, lambda m: m[1] + m[2] + digest + m[2] + m[4], block, flags=re.MULTILINE)
        updated = "".join(lines[:start]) + changed + "".join(lines[end:])
        if updated != original:
            changes[path] = updated
    return changes


class GitHub:
    def __init__(self, token: str):
        if not token:
            raise ValueError("GITHUB_TOKEN from the CLOUD-only GitHub App credential is required")
        self.token = token

    def request(self, method: str, endpoint: str, payload: dict | None = None, *, missing_ok: bool = False):
        request = urllib.request.Request(
            f"https://api.github.com/repos/{GITOPS_REPOSITORY}/{endpoint}",
            data=json.dumps(payload).encode() if payload is not None else None,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "star23-ai-jenkins",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if missing_ok and error.code == 404:
                return None
            # Never print request headers, tokens or arbitrary API response bodies.
            raise RuntimeError(f"GitHub {method} {endpoint}: HTTP {error.code}") from None


def read_values(github: GitHub, revision: str) -> dict[str, str]:
    files = {}
    for path in VALUES_PATHS:
        response = github.request("GET", f"contents/{path}?ref={revision}")
        if response.get("type") != "file" or response.get("encoding") != "base64":
            raise ValueError(f"expected an ordinary base64-encoded values file: {path}")
        files[path] = base64.b64decode(response["content"]).decode("utf-8")
    return files


def metadata(source_sha: str, digest: str, status: str) -> dict:
    return {
        "status": status,
        "source_sha": source_sha,
        "image": f"{IMAGE_REPOSITORY}@{digest}",
        "digest": digest,
        "gitops_repository": GITOPS_REPOSITORY,
        "base_branch": "main",
    }


def create_release_pr(github: GitHub, source_sha: str, digest: str) -> dict:
    validate_revision(source_sha, digest)
    if os.environ.get("BRANCH_NAME") != "main" or os.environ.get("CHANGE_ID"):
        raise ValueError("production PR creation requires a main branch build, never a pull request")
    base_sha = github.request("GET", "git/ref/heads/main")["object"]["sha"]
    files = read_values(github, base_sha)
    changes = plan_changes(files, digest)
    result = metadata(source_sha, digest, "unchanged")
    result["cloud_base_sha"] = base_sha
    result["changed_paths"] = list(changes)
    if not changes:
        return result

    branch = f"jenkins/ai-prod-{source_sha}-{digest[7:19]}"
    existing = github.request("GET", f"git/ref/heads/{branch}", missing_ok=True)
    if existing:
        revision = existing["object"]["sha"]
        comparison = github.request("GET", f"compare/{base_sha}...{revision}")
        changed_paths = {item["filename"] for item in comparison["files"]}
        if changed_paths != set(changes) or read_values(github, revision) != {**files, **changes}:
            raise ValueError("existing release branch differs from this rollout; review it manually")
        query = urllib.parse.urlencode({"state": "all", "head": f"100-hours-a-week:{branch}", "base": "main"})
        pulls = github.request("GET", f"pulls?{query}")
        if pulls:
            if pulls[0]["state"] != "open":
                raise ValueError("the release PR is closed; review deployment state before retrying")
            result.update(status="pr-open", pull_request_url=pulls[0]["html_url"])
            return result
    else:
        base_tree = github.request("GET", f"git/commits/{base_sha}")["tree"]["sha"]
        tree = github.request(
            "POST",
            "git/trees",
            {
                "base_tree": base_tree,
                "tree": [
                    {"path": path, "mode": "100644", "type": "blob", "content": content}
                    for path, content in changes.items()
                ],
            },
        )
        commit = github.request(
            "POST",
            "git/commits",
            {"message": f"chore(ai): deploy {source_sha}", "tree": tree["sha"], "parents": [base_sha]},
        )
        # Create only. Never update/force-push main or an existing release branch.
        github.request("POST", "git/refs", {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})

    pull = github.request(
        "POST",
        "pulls",
        {
            "title": f"chore(ai): deploy {source_sha[:12]} to production",
            "head": branch,
            "base": "main",
            "body": (
                f"Deploy AI source `{source_sha}` using `{IMAGE_REPOSITORY}@{digest}`.\n\n"
                "Jenkins validated and published this image. This PR changes only the five AI image digests.\n\n"
                "Before merge, verify any required Alembic migration with this exact image and review other pending AI releases. "
                "CLOUD has no AI migration hook. Merge to main allows Argo CD to roll out the five services; "
                "check Synced/Healthy and /health afterwards. Revert this PR to restore the previous image; "
                "database rollback requires separate review."
            ),
        },
    )
    result.update(status="pr-open", pull_request_url=pull["html_url"])
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--cloud-dir", type=Path, help="offline mode: read a local CLOUD checkout without modifying it")
    parser.add_argument("--create-pr", action="store_true", help="explicitly allow a new CLOUD branch/commit/PR")
    parser.add_argument("--output-dir", type=Path, default=Path(".ci-reports"))
    args = parser.parse_args(argv)
    validate_revision(args.source_sha, args.digest)
    if args.create_pr and args.cloud_dir:
        parser.error("--cloud-dir is offline only; it cannot be combined with --create-pr")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.create_pr:
        result = create_release_pr(GitHub(os.environ.get("GITHUB_TOKEN", "")), args.source_sha, args.digest)
    else:
        if not args.cloud_dir:
            parser.error("specify --cloud-dir for offline planning, or --create-pr for publication")
        files = {path: (args.cloud_dir / path).read_text(encoding="utf-8") for path in VALUES_PATHS}
        changes = plan_changes(files, args.digest)
        diff = "".join(
            "".join(
                difflib.unified_diff(
                    files[path].splitlines(keepends=True), content.splitlines(keepends=True), f"a/{path}", f"b/{path}"
                )
            )
            for path, content in changes.items()
        )
        (args.output_dir / "gitops.patch").write_text(diff, encoding="utf-8")
        result = metadata(args.source_sha, args.digest, "planned")
        result["changed_paths"] = list(changes)
    (args.output_dir / "gitops.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"GitOps release failed: {exc}", file=sys.stderr)
        sys.exit(1)
