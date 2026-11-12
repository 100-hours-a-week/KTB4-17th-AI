"""Derive deployment policy from the trusted Jenkins branch and one-time server flags."""

from __future__ import annotations

import argparse
import os
import sys

from scripts.jenkins.gitops_release import SOURCE_REPOSITORY, environment_for_branch


def build_policy(
    *,
    branch: str,
    change_id: str = "",
    change_fork: str = "",
    repository: str = SOURCE_REPOSITORY,
    staging_enabled: str = "false",
    prod_enabled: str = "false",
    manual_push: bool = False,
    manual_pr: bool = False,
) -> dict:
    environment = environment_for_branch(branch, change_id=change_id, change_fork=change_fork)
    if repository != SOURCE_REPOSITORY:
        raise ValueError("deployment requires the original AI repository")
    if any(flag not in ("", "false", "true") for flag in (staging_enabled, prod_enabled)):
        raise ValueError("AI environment activation flags must be exactly true or false")
    auto_deploy = (staging_enabled if environment == "staging" else prod_enabled) == "true"
    if manual_pr and (branch != "main" or not manual_push or auto_deploy):
        raise ValueError("manual PR mode requires main, explicit publish and disabled automatic prod deployment")
    return {
        "environment": environment,
        "auto_deploy": auto_deploy,
        "publish": auto_deploy or manual_push,
        "manual_pr": manual_pr,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manual-push", action="store_true")
    parser.add_argument("--manual-pr", action="store_true")
    args = parser.parse_args()
    result = build_policy(
        branch=os.environ.get("BRANCH_NAME", ""),
        change_id=os.environ.get("CHANGE_ID", ""),
        change_fork=os.environ.get("CHANGE_FORK", ""),
        repository=os.environ.get("SOURCE_REPOSITORY", ""),
        staging_enabled=os.environ.get("AI_STAGING_AUTO_DEPLOY_ENABLED", "false"),
        prod_enabled=os.environ.get("AI_PROD_AUTO_DEPLOY_ENABLED", "false"),
        manual_push=args.manual_push,
        manual_pr=args.manual_pr,
    )
    print(" ".join(str(result[key]).lower() for key in ("environment", "auto_deploy", "publish", "manual_pr")))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ValueError as error:
        print(f"Deployment policy rejected: {error}", file=sys.stderr)
        sys.exit(1)
