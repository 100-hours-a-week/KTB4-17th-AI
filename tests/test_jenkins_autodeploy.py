from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from scripts.jenkins import gitops_release as release

SOURCE = "a" * 40
BASE = "b" * 40
DIGEST = "sha256:" + "c" * 64
OLD = "sha256:" + "d" * 64
OTHER = "sha256:" + "e" * 64
SOURCE_REPO = "100-hours-a-week/KTB4-17th-AI"
CLOUD_REPO = "100-hours-a-week/KTB4-17th-CLOUD"
ROLES = (
    "onboarding-persona-ai",
    "onboarding-face-certify-ai",
    "chat-ai-practice",
    "chat-ai-simulation",
    "chat-ai-cleanup",
)
PROD = tuple(f"manifest/helm/star23-service/values/{role}.yaml" for role in ROLES)
STAGING = tuple(f"manifest/helm/star23-service/values/staging/{role}.yaml" for role in ROLES)
VALUES = (
    "# keep comment\nimage:\n"
    "  repository: 975779326240.dkr.ecr.ap-northeast-2.amazonaws.com/star23/ai\n"
    f'  digest: "{OLD}" # keep quote\n  tag: "v0.9.0"\nresources:\n  limits:\n    cpu: 1\n'
)


class SourceGitHub:
    repository = SOURCE_REPO

    def __init__(self, heads=(SOURCE,)):
        self.heads = iter(heads)
        self.last = SOURCE
        self.calls = []

    def request(self, method, endpoint, payload=None):
        assert method == "GET"
        self.calls.append(endpoint)
        if endpoint.startswith("actions/workflows/"):
            return {
                "workflow_runs": [
                    {
                        "id": 1,
                        "run_number": 1,
                        "run_attempt": 1,
                        "head_sha": SOURCE,
                        "head_branch": "main",
                        "event": "push",
                        "path": ".github/workflows/release.yml",
                        "status": "completed",
                        "conclusion": "success",
                        "repository": {"full_name": SOURCE_REPO},
                        "head_repository": {"full_name": SOURCE_REPO},
                        "html_url": "https://github.com/actions/runs/1",
                    }
                ]
            }
        if endpoint == "git/matching-refs/tags/v":
            return []
        assert endpoint in ("git/ref/heads/dev", "git/ref/heads/main")
        self.last = next(self.heads, self.last)
        return {"object": {"sha": self.last}}


def publish(cloud, source, sha, digest, *, branch):
    tag = ("dev-" if branch == "dev" else "sha-") + sha
    return release.publish_release(cloud, source, sha, digest, branch=branch, image_tag=tag)


def updated_values(branch="dev"):
    tag = ("dev-" if branch == "dev" else "sha-") + SOURCE
    return VALUES.replace(OLD, DIGEST).replace("v0.9.0", tag)


class CloudGitHub:
    repository = CLOUD_REPO

    def __init__(self, *, files=None, conflict=None, fail=None):
        self.head = BASE
        self.files = {BASE: files if files is not None else dict.fromkeys(PROD + STAGING, VALUES)}
        self.parents = {}
        self.trees = {}
        self.writes = []
        self.conflict = conflict
        self.fail = fail
        self.updates = 0

    def request(self, method, endpoint, payload=None):
        if method != "GET":
            self.writes.append((method, endpoint, payload))
        if endpoint == self.fail:
            raise release.GitHubError(method, endpoint, 403)
        if method == "GET" and endpoint == "git/ref/heads/main":
            return {"object": {"sha": self.head}}
        if method == "GET" and endpoint.startswith("contents/"):
            path, revision = endpoint[9:].split("?ref=")
            if path not in self.files[revision]:
                raise release.GitHubError(method, endpoint, 404)
            return {
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(self.files[revision][path].encode()).decode(),
            }
        if method == "GET" and endpoint.startswith("git/commits/"):
            return {"tree": {"sha": endpoint.split("/")[-1]}}
        if method == "POST" and endpoint == "git/trees":
            tree = f"tree-{len(self.trees)}"
            self.trees[tree] = {
                **self.files[payload["base_tree"]],
                **{entry["path"]: entry["content"] for entry in payload["tree"]},
            }
            return {"sha": tree}
        if method == "POST" and endpoint == "git/commits":
            sha = f"{len(self.parents) + 1:040x}"
            self.parents[sha] = payload["parents"][0]
            self.files[sha] = self.trees[payload["tree"]]
            return {"sha": sha}
        if method == "PATCH" and endpoint == "git/refs/heads/main":
            self.updates += 1
            assert payload["force"] is False
            if self.conflict:
                self.conflict(self)
            if self.parents[payload["sha"]] != self.head:
                raise release.GitHubError(method, endpoint, 422)
            self.head = payload["sha"]
            return {"object": {"sha": self.head}}
        raise AssertionError((method, endpoint, payload))


@pytest.fixture(autouse=True)
def trusted_build(monkeypatch):
    monkeypatch.delenv("CHANGE_ID", raising=False)
    monkeypatch.delenv("CHANGE_FORK", raising=False)


@pytest.mark.parametrize("branch,environment,paths", [("dev", "staging", STAGING), ("main", "prod", PROD)])
def test_branch_selects_only_its_five_digest_fields(branch, environment, paths):
    cloud, source = CloudGitHub(), SourceGitHub()
    result = publish(cloud, source, SOURCE, DIGEST, branch=branch)
    assert result["status"] == "cloud-updated"
    assert result["environment"] == environment
    assert result["cloud_commit"] == cloud.head
    assert set(result["changed_paths"]) == set(paths)
    for path, content in cloud.files[cloud.head].items():
        assert content == (updated_values(branch) if path in paths else VALUES)
    assert [endpoint for _, endpoint, _ in cloud.writes] == ["git/trees", "git/commits", "git/refs/heads/main"]
    assert [call for call in source.calls if call.startswith("git/ref/")] == [
        f"git/ref/heads/{branch}",
        f"git/ref/heads/{branch}",
    ]


@pytest.mark.parametrize(
    "branch,change_id,change_fork", [("feat/test", "", ""), ("dev", "17", ""), ("main", "", "fork")]
)
def test_untrusted_build_cannot_write_cloud(monkeypatch, branch, change_id, change_fork):
    monkeypatch.setenv("CHANGE_ID", change_id)
    monkeypatch.setenv("CHANGE_FORK", change_fork)
    cloud = CloudGitHub()
    with pytest.raises(ValueError):
        publish(cloud, SourceGitHub(), SOURCE, DIGEST, branch=branch)
    assert cloud.writes == []


@pytest.mark.parametrize("sha,digest", [("dev", DIGEST), (SOURCE, "latest"), (SOURCE, DIGEST + "\n")])
def test_bad_revision_rejected_before_writes(sha, digest):
    cloud = CloudGitHub()
    with pytest.raises(ValueError):
        publish(cloud, SourceGitHub(), sha, digest, branch="dev")
    assert cloud.writes == []


@pytest.mark.parametrize("which", ["source", "cloud"])
def test_foreign_repository_credential_is_rejected(which):
    cloud, source = CloudGitHub(), SourceGitHub()
    (source if which == "source" else cloud).repository = "foreign/repository"
    with pytest.raises(ValueError):
        publish(cloud, source, SOURCE, DIGEST, branch="dev")
    assert cloud.writes == []


def test_missing_staging_files_never_fall_back_to_prod():
    cloud = CloudGitHub(files=dict.fromkeys(PROD, VALUES))
    with pytest.raises(RuntimeError, match="404"):
        publish(cloud, SourceGitHub(), SOURCE, DIGEST, branch="dev")
    assert cloud.writes == []


@pytest.mark.parametrize("values", [VALUES + "image:\n  digest: bad\n", VALUES.replace("  digest:", '  "digest":')])
def test_ambiguous_staging_yaml_prevents_writes(values):
    cloud = CloudGitHub(files=dict.fromkeys(STAGING, values))
    with pytest.raises(ValueError):
        publish(cloud, SourceGitHub(), SOURCE, DIGEST, branch="dev")
    assert cloud.writes == []


def test_already_current_digest_is_idempotent_and_still_checks_source():
    cloud = CloudGitHub(files=dict.fromkeys(STAGING, updated_values()))
    source = SourceGitHub()
    result = publish(cloud, source, SOURCE, DIGEST, branch="dev")
    assert result["status"] == "unchanged"
    assert result["cloud_commit"] == BASE
    assert cloud.writes == []
    assert source.calls


def test_old_dev_sha_cannot_roll_back_latest_release():
    cloud = CloudGitHub()
    with pytest.raises(RuntimeError, match="stale source"):
        publish(cloud, SourceGitHub(("f" * 40,)), SOURCE, DIGEST, branch="dev")
    assert cloud.writes == []


def test_source_advancing_during_preparation_prevents_ref_update():
    cloud = CloudGitHub()
    with pytest.raises(RuntimeError, match="stale source"):
        publish(cloud, SourceGitHub((SOURCE, "f" * 40)), SOURCE, DIGEST, branch="dev")
    assert not any(method == "PATCH" for method, _, _ in cloud.writes)
    assert cloud.head == BASE


def advance_cloud(cloud, *, competing=False):
    if cloud.updates > 1:
        return
    updated = dict(cloud.files[cloud.head])
    updated[STAGING[0]] = updated[STAGING[0]].replace(OLD, OTHER) if competing else VALUES.replace("cpu: 1", "cpu: 2")
    updated["unrelated.yaml"] = "keep this concurrent change\n"
    cloud.head = "f" * 40
    cloud.files[cloud.head] = updated


def test_unrelated_concurrent_change_is_preserved_in_retry():
    cloud = CloudGitHub(conflict=advance_cloud)
    result = publish(cloud, SourceGitHub(), SOURCE, DIGEST, branch="dev")
    assert result["status"] == "cloud-updated"
    assert cloud.updates == 2
    assert cloud.files[cloud.head]["unrelated.yaml"] == "keep this concurrent change\n"
    assert "cpu: 2" in cloud.files[cloud.head][STAGING[0]]
    assert all(OLD not in cloud.files[cloud.head][path] for path in STAGING)


def test_competing_release_after_conflict_is_never_overwritten():
    cloud = CloudGitHub(conflict=lambda c: advance_cloud(c, competing=True))
    with pytest.raises(RuntimeError, match="another release"):
        publish(cloud, SourceGitHub(), SOURCE, DIGEST, branch="dev")
    assert cloud.updates == 1
    assert OTHER in cloud.files[cloud.head][STAGING[0]]


@pytest.mark.parametrize("endpoint", ["git/trees", "git/commits", "git/refs/heads/main"])
def test_permission_or_protection_error_remains_failure_without_pr_fallback(endpoint):
    cloud = CloudGitHub(fail=endpoint)
    with pytest.raises(RuntimeError, match="403"):
        publish(cloud, SourceGitHub(), SOURCE, DIGEST, branch="dev")
    assert cloud.head == BASE
    assert not any(path in ("pulls", "git/refs") for _, path, _ in cloud.writes)


def test_repeated_concurrency_conflicts_have_a_bounded_failure():
    def always_advance(cloud):
        current = cloud.files[cloud.head]
        cloud.head = f"{100 + cloud.updates:040x}"
        cloud.files[cloud.head] = dict(current)

    cloud = CloudGitHub(conflict=always_advance)
    with pytest.raises(RuntimeError, match="concurrent"):
        publish(cloud, SourceGitHub(), SOURCE, DIGEST, branch="dev")
    assert cloud.updates == 3


def test_only_tag_change_with_same_digest_still_produces_atomic_commit():
    cloud = CloudGitHub(files=dict.fromkeys(STAGING, VALUES.replace(OLD, DIGEST)))
    result = publish(cloud, SourceGitHub(), SOURCE, DIGEST, branch="dev")
    assert result["status"] == "cloud-updated"
    assert result["image_tag"] == "dev-" + SOURCE
    assert all(cloud.files[cloud.head][path] == updated_values() for path in STAGING)


def test_competing_tag_only_release_is_not_overwritten_on_retry():
    def change_tag(cloud):
        if cloud.updates == 1:
            cloud.files["f" * 40] = {**cloud.files[cloud.head], STAGING[0]: VALUES.replace("v0.9.0", "dev-" + "f" * 40)}
            cloud.head = "f" * 40

    cloud = CloudGitHub(conflict=change_tag)
    with pytest.raises(RuntimeError, match="another release"):
        publish(cloud, SourceGitHub(), SOURCE, DIGEST, branch="dev")
    assert cloud.updates == 1


def test_injected_prod_version_or_wrong_dev_sha_cannot_change_cloud():
    for branch, tag in [("dev", "dev-" + "f" * 40), ("main", "v9.9.9")]:
        cloud = CloudGitHub()
        with pytest.raises(ValueError):
            release.publish_release(cloud, SourceGitHub(), SOURCE, DIGEST, branch=branch, image_tag=tag)
        assert cloud.writes == []


def test_missing_tag_is_added_without_reserializing_other_yaml_fields():
    without_tag = VALUES.replace('  tag: "v0.9.0"\n', "")
    changes = release.plan_changes(dict.fromkeys(STAGING, without_tag), DIGEST, "staging", image_tag="dev-" + SOURCE)
    assert all(content == updated_values() for content in changes.values())


def test_source_advancing_while_final_release_check_waits_cannot_update_cloud():
    class AdvancingSource(SourceGitHub):
        def __init__(self):
            super().__init__()
            self.version_checks = 0

        def request(self, method, endpoint, payload=None):
            if endpoint.startswith("actions/workflows/"):
                self.version_checks += 1
                if self.version_checks == 2:
                    self.last = "f" * 40
            return super().request(method, endpoint, payload)

    cloud = CloudGitHub()
    with pytest.raises(RuntimeError, match="stale source"):
        publish(cloud, AdvancingSource(), SOURCE, DIGEST, branch="main")
    assert not any(method == "PATCH" for method, _, _ in cloud.writes)


def test_offline_dev_patch_has_only_staging_paths_and_reports_environment(tmp_path: Path):
    for path in STAGING:
        target = tmp_path / "cloud" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(VALUES)
    reports = tmp_path / "reports"
    assert (
        release.main(
            [
                "--cloud-dir",
                str(tmp_path / "cloud"),
                "--source-branch",
                "dev",
                "--source-sha",
                SOURCE,
                "--digest",
                DIGEST,
                "--image-tag",
                "dev-" + SOURCE,
                "--output-dir",
                str(reports),
            ]
        )
        == 0
    )
    result = json.loads((reports / "gitops.json").read_text())
    assert result["environment"] == "staging"
    assert result["applications"] == [f"staging-{role}" for role in ROLES]
    assert set(result["changed_paths"]) == set(STAGING)
    assert (reports / "gitops.patch").read_text().count(f'+  digest: "{DIGEST}"') == 5
