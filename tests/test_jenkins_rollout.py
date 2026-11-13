from __future__ import annotations

import copy
import importlib

import pytest

ROLES = (
    "onboarding-persona-ai",
    "onboarding-face-certify-ai",
    "chat-ai-practice",
    "chat-ai-simulation",
    "chat-ai-cleanup",
)
REVISION = "a" * 40
STAGING_SERVER = "https://FD1D5E0955692E6EA1E41FADBE848111.gr7.ap-northeast-2.eks.amazonaws.com"


def applications(branch="dev"):
    staging = branch == "dev"
    data = {
        "items": [
            {
                "metadata": {"name": role, "namespace": "argocd"},
                "spec": {
                    "project": "star23-apps",
                    "syncPolicy": {"automated": {"enabled": True}},
                    "destination": {
                        "server": "https://kubernetes.default.svc",
                        "namespace": "user" if role.startswith("onboarding-") else "chat",
                    },
                    "source": {
                        "repoURL": "https://github.com/100-hours-a-week/KTB4-17th-CLOUD.git",
                        "targetRevision": "main",
                        "path": "manifest/helm/star23-service",
                        "helm": {"valueFiles": [f"values/{'staging/' if staging else ''}{role}.yaml"]},
                    },
                },
                "status": {"sync": {"status": "Synced", "revision": REVISION}, "health": {"status": "Healthy"}},
            }
            for role in ROLES
        ]
    }

    for item in data["items"]:
        item["status"]["sync"]["comparedTo"] = copy.deepcopy(item["spec"])
    return data


def verify(data, branch="dev", revision=REVISION):
    module = importlib.import_module("scripts.jenkins.observe_rollout")
    return module.verify_applications(data, branch=branch, cloud_commit=revision)


@pytest.mark.parametrize("branch", ["dev", "main"])
def test_same_revision_synced_healthy_requires_all_five_correct_destinations(branch):
    result = verify(applications(branch), branch)
    assert result["status"] == "healthy"
    assert result["cloud_commit"] == REVISION
    assert len(result["applications"]) == 5


@pytest.mark.parametrize(
    "mutation", ["revision", "health", "sync", "server", "namespace", "values", "repo", "missing", "duplicate"]
)
def test_incomplete_or_wrong_environment_rollout_is_not_success(mutation):
    data = copy.deepcopy(applications())
    app = data["items"][0]
    if mutation == "revision":
        app["status"]["sync"]["revision"] = "b" * 40
    elif mutation in ("health", "sync"):
        app["status"][mutation]["status"] = "Progressing"
    elif mutation in ("server", "namespace"):
        app["spec"]["destination"][mutation] = STAGING_SERVER if mutation == "server" else "default"
    elif mutation == "values":
        app["spec"]["source"]["helm"]["valueFiles"] = ["values/onboarding-persona-ai.yaml"]
    elif mutation == "repo":
        app["spec"]["source"]["repoURL"] = "https://github.com/foreign/CLOUD.git"
    elif mutation == "missing":
        data["items"].pop()
    elif mutation == "duplicate":
        data["items"][-1] = app
    with pytest.raises((ValueError, RuntimeError)):
        verify(data)


def test_feature_branch_and_unpinned_revision_are_rejected():
    with pytest.raises(ValueError):
        verify(applications(), "feat/test")
    with pytest.raises(ValueError):
        verify(applications(), revision="main")


@pytest.mark.parametrize("field", ["source", "destination"])
def test_stale_status_from_other_source_or_destination_cannot_certify_rollout(field):
    data = applications()
    compared = data["items"][0]["status"]["sync"]["comparedTo"]
    if field == "destination":
        compared[field]["server"] = STAGING_SERVER
    else:
        compared[field]["helm"]["valueFiles"] = ["values/onboarding-persona-ai.yaml"]
    with pytest.raises((ValueError, RuntimeError)):
        verify(data)


@pytest.mark.parametrize(
    "override,value",
    [
        ("parameters", [{"name": "image.digest", "value": "sha256:" + "f" * 64}]),
        ("values", "image:\n  tag: injected\n"),
        ("valuesObject", {"image": {"tag": "injected"}}),
        ("fileParameters", [{"name": "image.digest", "path": "override.txt"}]),
    ],
)
def test_helm_overrides_cannot_certify_an_image_other_than_committed_values(override, value):
    data = applications()
    data["items"][0]["spec"]["source"]["helm"][override] = value
    with pytest.raises(ValueError):
        verify(data)


def test_multisource_override_is_rejected_by_single_source_contract():
    data = applications()
    data["items"][0]["spec"]["sources"] = [{"repoURL": "https://github.com/foreign/repo"}]
    with pytest.raises(ValueError):
        verify(data)


@pytest.mark.parametrize(
    "mutation", ["failed-sync", "sync-error", "auto-disabled", "auto-missing", "project", "app-namespace"]
)
def test_failed_sync_or_missing_automatic_deployment_contract_is_rejected(mutation):
    data = applications()
    app = data["items"][0]
    if mutation == "failed-sync":
        app["status"]["operationState"] = {"phase": "Failed", "syncResult": {"revision": REVISION}}
    elif mutation == "sync-error":
        app["status"]["conditions"] = [{"type": "SyncError"}]
    elif mutation == "auto-disabled":
        app["spec"]["syncPolicy"]["automated"]["enabled"] = False
    elif mutation == "auto-missing":
        del app["spec"]["syncPolicy"]
    elif mutation == "project":
        app["spec"]["project"] = "default"
    else:
        app["metadata"]["namespace"] = "default"
    with pytest.raises(ValueError):
        verify(data)
