from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.jenkins import gitops_release as release

SOURCE_SHA = "a" * 40
TAG = "sha-" + SOURCE_SHA
BASE_SHA = "b" * 40
DIGEST = "sha256:" + "c" * 64
OLD_DIGEST = "sha256:" + "d" * 64
IMAGE = "975779326240.dkr.ecr.ap-northeast-2.amazonaws.com/star23/ai"
ROLES = (
    "onboarding-persona-ai",
    "onboarding-face-certify-ai",
    "chat-ai-practice",
    "chat-ai-simulation",
    "chat-ai-cleanup",
)
PATHS = tuple(f"manifest/helm/star23-service/values/{role}.yaml" for role in ROLES)
VALUES = f"# preserve this comment\nimage:\n  digest: {OLD_DIGEST}\n  repository: {IMAGE}\n  tag: \"v0.9.0\"\nresources:\n  limits:\n    cpu: '1'\n"


def test_plan_changes_only_the_five_ai_digests_and_preserves_other_values() -> None:
    changes = release.plan_changes(dict.fromkeys(PATHS, VALUES), DIGEST, image_tag=TAG)

    assert set(changes) == set(PATHS)
    for content in changes.values():
        assert content == VALUES.replace(OLD_DIGEST, DIGEST).replace("v0.9.0", TAG)


@pytest.mark.parametrize(
    "values",
    [
        VALUES.replace("star23/ai", "star23/be"),
        VALUES.replace("  digest:", "  tag:"),
        VALUES + f"image:\n  digest: {OLD_DIGEST}\n  repository: {IMAGE}\n",
        VALUES.replace(OLD_DIGEST, "latest"),
    ],
)
def test_invalid_or_foreign_values_are_rejected(values: str) -> None:
    with pytest.raises(ValueError):
        release.plan_changes(dict.fromkeys(PATHS, values), DIGEST, image_tag=TAG)


def test_missing_target_file_is_rejected() -> None:
    with pytest.raises(ValueError):
        release.plan_changes({PATHS[0]: VALUES}, DIGEST, image_tag=TAG)


@pytest.mark.parametrize(
    "override",
    [
        f"image: {{digest: {OLD_DIGEST}, repository: foreign.example/ai}}\n",
        f"image: # override\n  digest: {OLD_DIGEST}\n  repository: foreign.example/ai\n",
        f"'image':\n  digest: {OLD_DIGEST}\n  repository: foreign.example/ai\n",
        f'"im\\x61ge":\n  digest: {OLD_DIGEST}\n  repository: foreign.example/ai\n',
        f"image :\n  digest: {OLD_DIGEST}\n  repository: foreign.example/ai\n",
    ],
)
def test_duplicate_or_escaped_image_mapping_prevents_api_writes(monkeypatch, override: str) -> None:
    monkeypatch.setenv("BRANCH_NAME", "main")
    github = FakeGitHub(values=VALUES + override)
    with pytest.raises(ValueError):
        release.create_release_pr(github, SOURCE_SHA, DIGEST, image_tag=TAG, source=FakeSource())
    assert github.writes == []


@pytest.mark.parametrize("key", ['"digest"', '"di\\x67est"', "'repository'", "digest"])
def test_duplicate_or_escaped_image_fields_are_rejected(key: str) -> None:
    values = VALUES.replace("resources:", f"  {key}: wrong-value\nresources:")
    with pytest.raises(ValueError):
        release.plan_changes(dict.fromkeys(PATHS, values), DIGEST, image_tag=TAG)


class FakeSource:
    repository = "100-hours-a-week/KTB4-17th-AI"

    def request(self, method, endpoint):
        assert method == "GET"
        if endpoint == "git/ref/heads/main":
            return {"object": {"sha": SOURCE_SHA}}
        if endpoint == "git/matching-refs/tags/v":
            return []
        assert endpoint.startswith("actions/workflows/release.yml/runs?")
        repo = {"full_name": self.repository}
        return {
            "workflow_runs": [
                {
                    "id": 1,
                    "run_number": 1,
                    "head_sha": SOURCE_SHA,
                    "head_branch": "main",
                    "event": "push",
                    "path": ".github/workflows/release.yml",
                    "status": "completed",
                    "conclusion": "success",
                    "repository": repo,
                    "head_repository": repo,
                    "html_url": "https://github.com/actions/runs/1",
                }
            ]
        }


class FakeGitHub:
    def __init__(
        self, *, unchanged: bool = False, existing: bool = False, fail_tree: bool = False, values: str = VALUES
    ):
        self.unchanged = unchanged
        self.existing = existing
        self.fail_tree = fail_tree
        self.values = values
        self.writes: list[tuple[str, dict]] = []

    def request(self, method: str, endpoint: str, payload: dict | None = None, *, missing_ok: bool = False):
        if method != "GET":
            self.writes.append((endpoint, payload))
        if method == "GET" and endpoint == "git/ref/heads/main":
            return {"object": {"sha": BASE_SHA}}
        if method == "GET" and endpoint == f"git/commits/{BASE_SHA}":
            return {"tree": {"sha": "base-tree"}}
        if method == "GET" and endpoint.startswith("contents/"):
            path, ref = endpoint.removeprefix("contents/").split("?ref=")
            assert path in PATHS
            assert ref in (BASE_SHA, "release-commit")
            content = (
                self.values.replace(OLD_DIGEST, DIGEST).replace("v0.9.0", TAG)
                if self.unchanged or ref == "release-commit"
                else self.values
            )
            return {"type": "file", "encoding": "base64", "content": base64.b64encode(content.encode()).decode()}
        if method == "GET" and endpoint.startswith("git/ref/heads/jenkins/"):
            assert missing_ok
            return {"object": {"sha": "release-commit"}} if self.existing else None
        if method == "GET" and endpoint.startswith("compare/"):
            return {"files": [{"filename": path, "status": "modified"} for path in PATHS]}
        if method == "GET" and endpoint.startswith("pulls?"):
            return [{"html_url": "https://github.com/100-hours-a-week/KTB4-17th-CLOUD/pull/123", "state": "open"}]
        if method == "POST" and endpoint == "git/trees":
            if self.fail_tree:
                raise RuntimeError("GitHub tree creation failed")
            return {"sha": "new-tree"}
        if method == "POST" and endpoint == "git/commits":
            return {"sha": "release-commit"}
        if method == "POST" and endpoint == "git/refs":
            return {"object": {"sha": "release-commit"}}
        if method == "POST" and endpoint == "pulls":
            return {"html_url": "https://github.com/100-hours-a-week/KTB4-17th-CLOUD/pull/123"}
        raise AssertionError(f"Unexpected GitHub call: {method} {endpoint}")


def test_release_creates_an_atomic_five_file_pr_without_updating_main(monkeypatch) -> None:
    monkeypatch.setenv("BRANCH_NAME", "main")
    monkeypatch.delenv("CHANGE_ID", raising=False)
    github = FakeGitHub()

    result = release.create_release_pr(github, SOURCE_SHA, DIGEST, image_tag=TAG, source=FakeSource())

    assert result["pull_request_url"].endswith("/pull/123")
    tree, commit, branch, pr = github.writes
    assert [call[0] for call in github.writes] == ["git/trees", "git/commits", "git/refs", "pulls"]
    assert tree[1]["base_tree"] == "base-tree"
    assert {entry["path"] for entry in tree[1]["tree"]} == set(PATHS)
    assert all(
        entry["content"] == VALUES.replace(OLD_DIGEST, DIGEST).replace("v0.9.0", TAG) for entry in tree[1]["tree"]
    )
    assert commit[1]["parents"] == [BASE_SHA]
    assert branch[1]["ref"].startswith("refs/heads/jenkins/ai-prod-")
    assert pr[1]["base"] == "main"
    assert pr[1]["head"] != "main"
    assert DIGEST in pr[1]["body"]


@pytest.mark.parametrize("branch,change_id", [("dev", ""), ("main", "17"), ("feature/x", "")])
def test_untrusted_source_branch_cannot_create_a_release(monkeypatch, branch: str, change_id: str) -> None:
    monkeypatch.setenv("BRANCH_NAME", branch)
    monkeypatch.setenv("CHANGE_ID", change_id)
    github = FakeGitHub()
    with pytest.raises(ValueError):
        release.create_release_pr(github, SOURCE_SHA, DIGEST, image_tag=TAG, source=FakeSource())
    assert github.writes == []


@pytest.mark.parametrize("sha,digest", [("main", DIGEST), (SOURCE_SHA, "sha256:bad"), (SOURCE_SHA, DIGEST + "\nboom")])
def test_invalid_revision_or_digest_prevents_api_writes(monkeypatch, sha: str, digest: str) -> None:
    monkeypatch.setenv("BRANCH_NAME", "main")
    github = FakeGitHub()
    with pytest.raises(ValueError):
        release.create_release_pr(github, sha, digest, image_tag=TAG, source=FakeSource())
    assert github.writes == []


def test_already_deployed_digest_is_a_noop(monkeypatch) -> None:
    monkeypatch.setenv("BRANCH_NAME", "main")
    github = FakeGitHub(unchanged=True)
    assert (
        release.create_release_pr(github, SOURCE_SHA, DIGEST, image_tag=TAG, source=FakeSource())["status"]
        == "unchanged"
    )
    assert github.writes == []


def test_matching_open_release_pr_is_reused_without_new_commits(monkeypatch) -> None:
    monkeypatch.setenv("BRANCH_NAME", "main")
    github = FakeGitHub(existing=True)
    assert release.create_release_pr(github, SOURCE_SHA, DIGEST, image_tag=TAG, source=FakeSource())[
        "pull_request_url"
    ].endswith("/pull/123")
    assert github.writes == []


def test_github_failure_does_not_create_pr(monkeypatch) -> None:
    monkeypatch.setenv("BRANCH_NAME", "main")
    github = FakeGitHub(fail_tree=True)
    with pytest.raises(RuntimeError, match="tree creation"):
        release.create_release_pr(github, SOURCE_SHA, DIGEST, image_tag=TAG, source=FakeSource())
    assert [endpoint for endpoint, _ in github.writes] == ["git/trees"]


def test_offline_cli_writes_patch_without_touching_cloud_or_using_credentials(tmp_path: Path) -> None:
    cloud = tmp_path / "cloud"
    for path in PATHS:
        target = cloud / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(VALUES)
    output = tmp_path / "reports"
    with patch.dict("os.environ", {"GITHUB_TOKEN": ""}):
        result = release.main(
            [
                "--cloud-dir",
                str(cloud),
                "--source-sha",
                SOURCE_SHA,
                "--digest",
                DIGEST,
                "--image-tag",
                TAG,
                "--output-dir",
                str(output),
            ]
        )
    assert result == 0
    patch_file = output / "gitops.patch"
    assert patch_file.read_text().count(f"+  digest: {DIGEST}") == 5
    assert json.loads((output / "gitops.json").read_text())["status"] == "planned"
    assert all((cloud / path).read_text() == VALUES for path in PATHS)
    applied = subprocess.run(["git", "apply", "--check", str(patch_file)], cwd=cloud, capture_output=True, text=True)
    assert applied.returncode == 0, applied.stderr
