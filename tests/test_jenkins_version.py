from __future__ import annotations

import importlib

import pytest

SHA = "a" * 40
OTHER = "b" * 40


def version_module():
    return importlib.import_module("scripts.jenkins.release_version")


def run(**overrides):
    return {
        "id": 123,
        "run_number": 9,
        "run_attempt": 1,
        "head_sha": SHA,
        "head_branch": "main",
        "event": "push",
        "path": ".github/workflows/release.yml",
        "status": "completed",
        "conclusion": "success",
        "repository": {"full_name": "100-hours-a-week/KTB4-17th-AI"},
        "head_repository": {"full_name": "100-hours-a-week/KTB4-17th-AI"},
        "html_url": "https://github.com/100-hours-a-week/KTB4-17th-AI/actions/runs/123",
        **overrides,
    }


class GitHub:
    repository = "100-hours-a-week/KTB4-17th-AI"

    def __init__(self, runs=None, refs=None, objects=None, denied=False):
        self.runs = runs if runs is not None else [[run()]]
        self.index = 0
        self.refs = refs or []
        self.objects = objects or {}
        self.calls = []
        self.denied = denied

    def request(self, method, endpoint):
        assert method == "GET"
        self.calls.append(endpoint)
        if self.denied:
            raise RuntimeError("GitHub GET: HTTP 403")
        if endpoint.startswith("actions/workflows/release.yml/runs?"):
            assert f"head_sha={SHA}" in endpoint
            rows = self.runs[min(self.index, len(self.runs) - 1)]
            self.index += 1
            return {"total_count": len(rows), "workflow_runs": rows}
        if endpoint == "git/matching-refs/tags/v":
            return self.refs
        if endpoint.startswith("git/tags/"):
            return {"object": self.objects[endpoint[9:]]}
        raise AssertionError(endpoint)


def ref(name, sha, kind="commit"):
    return {"ref": "refs/tags/" + name, "object": {"type": kind, "sha": sha}}


def resolve(github, branch="main"):
    elapsed = [0]
    return version_module().resolve_version(
        github,
        SHA,
        branch=branch,
        timeout=2,
        poll_interval=1,
        clock=lambda: elapsed[0],
        sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
    )


def test_dev_uses_full_sha_version_without_waiting_for_release_workflow():
    github = GitHub(denied=True)
    result = resolve(github, "dev")
    assert result["image_tag"] == "dev-" + SHA
    assert result["release_status"] == "development"
    assert github.calls == []


@pytest.mark.parametrize("annotated", [False, True])
def test_same_commit_lightweight_and_annotated_release_tags_are_verified(annotated):
    github = GitHub(
        refs=[ref("v3.1.0", OTHER if annotated else SHA, "tag" if annotated else "commit")],
        objects={OTHER: {"type": "commit", "sha": SHA}},
    )
    result = resolve(github)
    assert result["image_tag"] == "v3.1.0"
    assert result["release_tag"] == "v3.1.0"
    assert result["workflow_run_id"] == 123


def test_successful_no_release_commit_uses_its_own_sha_never_the_latest_past_release():
    result = resolve(GitHub(refs=[ref("v9.9.9", OTHER)]))
    assert result["image_tag"] == "sha-" + SHA
    assert result["release_status"] == "no-release"


def test_async_release_waits_until_that_sha_workflow_finishes():
    github = GitHub(runs=[[], [run(status="in_progress", conclusion=None)], [run()]], refs=[ref("v2.4.1", SHA)])
    assert resolve(github)["image_tag"] == "v2.4.1"
    assert github.index == 3


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", "skipped", "action_required"])
def test_failed_or_cancelled_release_cannot_fall_back_to_an_older_version(conclusion):
    with pytest.raises(RuntimeError, match="release workflow"):
        resolve(GitHub(runs=[[run(conclusion=conclusion)]], refs=[ref("v1.0.0", OTHER)]))


def test_latest_same_sha_run_is_authoritative_including_failed_reruns():
    with pytest.raises(RuntimeError):
        resolve(GitHub(runs=[[run(), run(id=124, run_number=10, conclusion="failure")]]))


@pytest.mark.parametrize("runs", [[[]], [[run(status="queued", conclusion=None)]], [[run(head_sha=OTHER)]]])
def test_missing_pending_or_wrong_sha_release_has_bounded_timeout(runs):
    with pytest.raises(RuntimeError, match="timed out"):
        resolve(GitHub(runs=runs))


def test_actions_query_failure_is_not_no_release():
    with pytest.raises(RuntimeError, match="403"):
        resolve(GitHub(denied=True))


def test_ambiguous_tags_and_cyclic_annotated_tags_are_rejected():
    with pytest.raises(RuntimeError):
        resolve(GitHub(refs=[ref("v1.0.0", SHA), ref("v2.0.0", SHA)]))
    with pytest.raises(RuntimeError):
        resolve(GitHub(refs=[ref("v1.0.0", OTHER, "tag")], objects={OTHER: {"type": "tag", "sha": OTHER}}))


@pytest.mark.parametrize(
    "branch,tag",
    [
        ("dev", "v3.1.0"),
        ("dev", "dev-" + OTHER),
        ("main", "latest"),
        ("main", "sha-" + OTHER),
        ("feature/x", "dev-" + SHA),
    ],
)
def test_injected_or_wrong_source_version_is_rejected(branch, tag):
    with pytest.raises(ValueError):
        version_module().validate_image_tag(tag, SHA, branch)


def test_invalid_source_sha_or_foreign_repository_never_queries_actions():
    github = GitHub()
    with pytest.raises(ValueError):
        version_module().resolve_version(github, "main", branch="main")
    github.repository = "foreign/repo"
    with pytest.raises(ValueError):
        resolve(github)
    assert github.calls == []
