from __future__ import annotations

import importlib
import os
import subprocess
import sys

import pytest


def policy(**kwargs):
    return importlib.import_module("scripts.jenkins.deployment_policy").build_policy(**kwargs)


@pytest.mark.parametrize("branch,environment", [("dev", "staging"), ("main", "prod")])
def test_initially_disabled_environment_still_runs_ci_only(branch, environment):
    assert policy(branch=branch) == {
        "environment": environment,
        "auto_deploy": False,
        "publish": False,
        "manual_pr": False,
    }


@pytest.mark.parametrize("branch,flags", [("dev", {"staging_enabled": "true"}), ("main", {"prod_enabled": "true"})])
def test_one_time_environment_activation_needs_no_per_build_checkbox(branch, flags):
    result = policy(branch=branch, **flags)
    assert result["auto_deploy"] is True
    assert result["publish"] is True
    assert result["manual_pr"] is False


@pytest.mark.parametrize("branch,flags", [("dev", {"prod_enabled": "true"}), ("main", {"staging_enabled": "true"})])
def test_other_environment_activation_cannot_enable_this_branch(branch, flags):
    assert policy(branch=branch, **flags)["auto_deploy"] is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"branch": "feature/test"},
        {"branch": "dev", "change_id": "42"},
        {"branch": "main", "change_fork": "attacker/repo"},
        {"branch": "dev", "repository": "foreign/AI"},
        {"branch": "dev", "staging_enabled": "yes"},
        {"branch": "dev", "prod_enabled": "TRUE"},
        {"branch": "dev", "manual_push": True, "manual_pr": True},
        {"branch": "main", "manual_pr": True},
        {"branch": "main", "prod_enabled": "true", "manual_push": True, "manual_pr": True},
    ],
)
def test_unsafe_or_ambiguous_activation_is_rejected(kwargs):
    with pytest.raises(ValueError):
        policy(**kwargs)


def test_explicit_manual_prod_pr_remains_separate_from_automatic_deployment():
    result = policy(branch="main", manual_push=True, manual_pr=True)
    assert result == {"environment": "prod", "auto_deploy": False, "publish": True, "manual_pr": True}


def test_cli_consumes_server_environment_and_emits_policy_without_tokens():
    result = subprocess.run(
        [sys.executable, "-m", "scripts.jenkins.deployment_policy"],
        env={
            **os.environ,
            "BRANCH_NAME": "dev",
            "CHANGE_ID": "",
            "CHANGE_FORK": "",
            "SOURCE_REPOSITORY": "100-hours-a-week/KTB4-17th-AI",
            "AI_STAGING_AUTO_DEPLOY_ENABLED": "true",
            "AI_PROD_AUTO_DEPLOY_ENABLED": "false",
            "GITHUB_TOKEN": "never-print-this",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "staging true true false"
    assert "never-print-this" not in result.stdout + result.stderr
