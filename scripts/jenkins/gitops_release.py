"""Plan AI digests, publish a branch-scoped CLOUD commit, or prepare a manual prod PR."""

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

if __package__ in (None, ""):
    from release_version import SOURCE_REPOSITORY, resolve_version, validate_image_tag
else:
    from .release_version import SOURCE_REPOSITORY, resolve_version, validate_image_tag

GITOPS_REPOSITORY = "100-hours-a-week/KTB4-17th-CLOUD"
IMAGE_REPOSITORY = "975779326240.dkr.ecr.ap-northeast-2.amazonaws.com/star23/ai"
ROLES = (
    "onboarding-persona-ai",
    "onboarding-face-certify-ai",
    "chat-ai-practice",
    "chat-ai-simulation",
    "chat-ai-cleanup",
)


def environment_for_branch(branch: str, *, change_id: str = "", change_fork: str = "") -> str:
    if branch not in ("dev", "main") or change_id or change_fork:
        raise ValueError("deployment requires the original dev/main branch, never a feature, fork or PR build")
    return "staging" if branch == "dev" else "prod"


def paths_for_environment(environment: str) -> tuple[str, ...]:
    if environment not in ("staging", "prod"):
        raise ValueError("environment must be staging or prod")
    prefix = "staging/" if environment == "staging" else ""
    return tuple(f"manifest/helm/star23-service/values/{prefix}{role}.yaml" for role in ROLES)


VALUES_PATHS = paths_for_environment("prod")  # Existing manual PR interface.


def validate_revision(source_sha: str, digest: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("source SHA must be the full checkout GIT_COMMIT")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("image digest must be sha256 followed by 64 lowercase hex characters")


def inspect_values(
    files: dict[str, str], digest: str, environment: str, *, image_tag: str
) -> tuple[dict[str, str], dict[str, tuple[str | None, str]]]:
    """Change only direct image.tag/digest scalars in the known CLOUD values layout.

    Reject an unfamiliar layout instead of reserializing YAML or touching other
    settings. This preserves comments, secret references and unrelated values.
    """
    validate_revision("0" * 40, digest)
    if set(files) != set(paths_for_environment(environment)):
        raise ValueError(f"exactly the five AI {environment} values files are required")
    allowed_tag = (
        r"dev-[0-9a-f]{40}"
        if environment == "staging"
        else r"(?:sha-[0-9a-f]{40}|v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))"
    )
    if not re.fullmatch(allowed_tag, image_tag):
        raise ValueError("unexpected environment-specific image tag")
    changes = {}
    previous_digests = {}
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
        tag_pattern = (
            r"^(  tag: *)(?:(['\"])([A-Za-z0-9_.-]*)\2|([A-Za-z0-9_][A-Za-z0-9_.-]{0,127}))([ \t]*(?:#.*)?\r?)$"
        )
        tag_matches = list(re.finditer(tag_pattern, block, re.MULTILINE))
        tag_keys = len(re.findall(r"^  tag:", block, re.MULTILINE))
        if tag_keys != len(tag_matches) or tag_keys > 1:
            raise ValueError(f"expected at most one ordinary image.tag in {path}")
        previous_tag = (tag_matches[0][3] if tag_matches[0][2] else tag_matches[0][4]) if tag_matches else None
        previous_digests[path] = (previous_tag, matches[0][3])
        changed = re.sub(pattern, lambda m: m[1] + m[2] + digest + m[2] + m[4], block, flags=re.MULTILINE)
        if tag_matches:
            changed = re.sub(
                tag_pattern,
                lambda m: m[1] + (m[2] or "") + image_tag + (m[2] or "") + m[5],
                changed,
                flags=re.MULTILINE,
            )
        else:
            match = re.search(pattern, changed, re.MULTILINE)
            position = match.end() - (1 if match[4].endswith("\r") else 0)
            newline = "\r\n" if "\r\n" in original else "\n"
            changed = changed[:position] + newline + f'  tag: "{image_tag}"' + changed[position:]
        updated = "".join(lines[:start]) + changed + "".join(lines[end:])
        if updated != original:
            changes[path] = updated
    return changes, previous_digests


def plan_changes(files: dict[str, str], digest: str, environment: str = "prod", *, image_tag: str) -> dict[str, str]:
    return inspect_values(files, digest, environment, image_tag=image_tag)[0]


class GitHubError(RuntimeError):
    def __init__(self, method: str, endpoint: str, status: int):
        self.status = status
        super().__init__(f"GitHub {method} {endpoint}: HTTP {status}")


class GitHub:
    def __init__(self, token: str, repository: str = GITOPS_REPOSITORY):
        if not token:
            raise ValueError("a repository-scoped GitHub App token is required")
        if repository not in (GITOPS_REPOSITORY, SOURCE_REPOSITORY):
            raise ValueError("unexpected GitHub repository")
        self.token = token
        self.repository = repository

    def request(self, method: str, endpoint: str, payload: dict | None = None, *, missing_ok: bool = False):
        if self.repository == SOURCE_REPOSITORY and method != "GET":
            raise ValueError("the source GitHub credential is read-only")
        request = urllib.request.Request(
            f"https://api.github.com/repos/{self.repository}/{endpoint}",
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
            raise GitHubError(method, endpoint, error.code) from None
        except urllib.error.URLError:
            raise RuntimeError(f"GitHub {method} {endpoint}: transport failure") from None


def read_values(github: GitHub, revision: str, environment: str = "prod") -> dict[str, str]:
    files = {}
    for path in paths_for_environment(environment):
        response = github.request("GET", f"contents/{path}?ref={revision}")
        if response.get("type") != "file" or response.get("encoding") != "base64":
            raise ValueError(f"expected an ordinary base64-encoded values file: {path}")
        files[path] = base64.b64decode(response["content"]).decode("utf-8")
    return files


def metadata(source_sha: str, digest: str, status: str, environment: str = "prod", *, image_tag: str) -> dict:
    paths_for_environment(environment)
    return {
        "status": status,
        "source_sha": source_sha,
        "image": f"{IMAGE_REPOSITORY}@{digest}",
        "digest": digest,
        "image_tag": image_tag,
        "gitops_repository": GITOPS_REPOSITORY,
        "base_branch": "main",
        "environment": environment,
        "cluster": "star-EKS-v3-staging" if environment == "staging" else "star-EKS-v3",
        "applications": list(ROLES),
    }


def require_current_source(source: GitHub, branch: str, source_sha: str) -> None:
    current = source.request("GET", f"git/ref/heads/{branch}")["object"]["sha"]
    if current != source_sha:
        raise RuntimeError(f"stale source SHA: {branch} no longer points at the validated commit")


def require_release_version(source: GitHub, source_sha: str, branch: str, image_tag: str) -> None:
    validate_image_tag(image_tag, source_sha, branch)
    if resolve_version(source, source_sha, branch=branch)["image_tag"] != image_tag:
        raise ValueError("image tag differs from this source SHA's verified semantic-release result")


def publish_release(
    github: GitHub, source: GitHub, source_sha: str, digest: str, *, branch: str, image_tag: str
) -> dict:
    """Non-force one atomic CLOUD/main commit; retry unrelated concurrent changes only."""
    validate_revision(source_sha, digest)
    environment = environment_for_branch(
        branch, change_id=os.environ.get("CHANGE_ID", ""), change_fork=os.environ.get("CHANGE_FORK", "")
    )
    if github.repository != GITOPS_REPOSITORY or source.repository != SOURCE_REPOSITORY:
        raise ValueError("separate CLOUD-write and original-source-read clients are required")
    require_release_version(source, source_sha, branch, image_tag)
    baseline_digests = None
    for attempt in range(3):
        require_current_source(source, branch, source_sha)
        base_sha = github.request("GET", "git/ref/heads/main")["object"]["sha"]
        files = read_values(github, base_sha, environment)
        changes, current_digests = inspect_values(files, digest, environment, image_tag=image_tag)
        result = metadata(source_sha, digest, "unchanged", environment, image_tag=image_tag)
        result.update(cloud_base_sha=base_sha, cloud_commit=base_sha, changed_paths=list(changes))
        if not changes:
            return result
        if baseline_digests is not None and current_digests != baseline_digests:
            raise RuntimeError("another release changed an AI target tag/digest; refusing to overwrite it")
        baseline_digests = current_digests
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
            {"message": f"chore(ai): deploy {source_sha} to {environment}", "tree": tree["sha"], "parents": [base_sha]},
        )
        # Recheck immediately before the sole ref mutation. GitHub has no atomic
        # transaction spanning the independent source and CLOUD repositories.
        require_release_version(source, source_sha, branch, image_tag)
        require_current_source(source, branch, source_sha)
        try:
            updated = github.request("PATCH", "git/refs/heads/main", {"sha": commit["sha"], "force": False})
        except GitHubError as error:
            if error.status not in (409, 422):
                raise
            latest = github.request("GET", "git/ref/heads/main")["object"]["sha"]
            if latest == base_sha:
                raise RuntimeError(
                    f"CLOUD main rejected non-force update (HTTP {error.status}); check App branch rules"
                ) from None
            if attempt == 2:
                raise RuntimeError("CLOUD main has repeated concurrent changes; retry from a fresh build") from None
            continue
        if updated.get("object", {}).get("sha") != commit["sha"]:
            raise RuntimeError("CLOUD returned an unexpected revision after the ref update")
        result.update(status="cloud-updated", cloud_commit=commit["sha"])
        return result
    raise RuntimeError("CLOUD update did not complete")


def create_release_pr(github: GitHub, source_sha: str, digest: str, *, image_tag: str, source: GitHub) -> dict:
    validate_revision(source_sha, digest)
    if os.environ.get("BRANCH_NAME") != "main" or os.environ.get("CHANGE_ID") or os.environ.get("CHANGE_FORK"):
        raise ValueError("production PR creation requires a main branch build, never a pull request")
    if source.repository != SOURCE_REPOSITORY:
        raise ValueError("manual release version must be read from the original AI repository")
    require_release_version(source, source_sha, "main", image_tag)
    require_current_source(source, "main", source_sha)
    base_sha = github.request("GET", "git/ref/heads/main")["object"]["sha"]
    files = read_values(github, base_sha)
    changes = plan_changes(files, digest, image_tag=image_tag)
    result = metadata(source_sha, digest, "unchanged", image_tag=image_tag)
    result["cloud_base_sha"] = base_sha
    result["changed_paths"] = list(changes)
    if not changes:
        return result

    branch = f"jenkins/ai-prod-{source_sha}-{image_tag}-{digest[7:19]}"
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
                f"Jenkins validated image tag `{image_tag}` at this digest. This PR changes only the five AI image tag/digest pairs.\n\n"
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
    parser.add_argument("--image-tag", required=True)
    parser.add_argument("--cloud-dir", type=Path, help="offline mode: read a local CLOUD checkout without modifying it")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--create-pr", action="store_true", help="manual main-only auxiliary PR mode")
    mode.add_argument("--apply", action="store_true", help="automatically commit to CLOUD/main for BRANCH_NAME")
    parser.add_argument("--source-branch", choices=("dev", "main"), help="offline planning only (default main)")
    parser.add_argument("--output-dir", type=Path, default=Path(".ci-reports"))
    args = parser.parse_args(argv)
    validate_revision(args.source_sha, args.digest)
    if (args.create_pr or args.apply) and (args.cloud_dir or args.source_branch):
        parser.error("remote modes use the trusted BRANCH_NAME; cloud-dir/source-branch are offline only")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.apply:
        result = publish_release(
            GitHub(os.environ.get("GITHUB_TOKEN", "")),
            GitHub(os.environ.get("SOURCE_GITHUB_TOKEN", ""), SOURCE_REPOSITORY),
            args.source_sha,
            args.digest,
            branch=os.environ.get("BRANCH_NAME", ""),
            image_tag=args.image_tag,
        )
    elif args.create_pr:
        result = create_release_pr(
            GitHub(os.environ.get("GITHUB_TOKEN", "")),
            args.source_sha,
            args.digest,
            image_tag=args.image_tag,
            source=GitHub(os.environ.get("SOURCE_GITHUB_TOKEN", ""), SOURCE_REPOSITORY),
        )
    else:
        if not args.cloud_dir:
            parser.error("specify --cloud-dir for offline planning, or --create-pr for publication")
        environment = environment_for_branch(args.source_branch or "main")
        validate_image_tag(args.image_tag, args.source_sha, args.source_branch or "main")
        files = {
            path: (args.cloud_dir / path).read_text(encoding="utf-8") for path in paths_for_environment(environment)
        }
        changes = plan_changes(files, args.digest, environment, image_tag=args.image_tag)
        diff = "".join(
            "".join(
                difflib.unified_diff(
                    files[path].splitlines(keepends=True), content.splitlines(keepends=True), f"a/{path}", f"b/{path}"
                )
            )
            for path, content in changes.items()
        )
        (args.output_dir / "gitops.patch").write_text(diff, encoding="utf-8")
        result = metadata(args.source_sha, args.digest, "planned", environment, image_tag=args.image_tag)
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
