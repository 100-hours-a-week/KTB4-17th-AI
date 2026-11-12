"""Read Argo Applications from its prod control cluster; never mutate either cluster."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from scripts.jenkins.gitops_release import ROLES, environment_for_branch, validate_revision

CONTROL_CONTEXT = "arn:aws:eks:ap-northeast-2:975779326240:cluster/star-EKS-v3"
STAGING_SERVER = "https://FD1D5E0955692E6EA1E41FADBE848111.gr7.ap-northeast-2.eks.amazonaws.com"


def verify_applications(data: dict, *, branch: str, cloud_commit: str) -> dict:
    environment = environment_for_branch(branch)
    validate_revision(cloud_commit, "sha256:" + "0" * 64)
    staging = environment == "staging"
    names = [f"staging-{role}" if staging else role for role in ROLES]
    items = data.get("items", [])
    if len(items) != 5 or {item.get("metadata", {}).get("name") for item in items} != set(names):
        raise ValueError("exactly the five environment-specific Argo Applications must be returned")
    for item in items:
        name = item["metadata"]["name"]
        role = name.removeprefix("staging-") if staging else name
        spec, status = item.get("spec", {}), item.get("status", {})
        expected_destination = {
            "server": STAGING_SERVER if staging else "https://kubernetes.default.svc",
            "namespace": "user" if role.startswith("onboarding-") else "chat",
        }
        source = spec.get("source", {})
        helm = source.get("helm", {})
        if spec.get("sources") or any(
            helm.get(key) for key in ("parameters", "values", "valuesObject", "fileParameters")
        ):
            raise ValueError(
                f"{name}: multiple sources and inline Helm overrides are outside the fixed values contract"
            )
        expected_values = [f"values/{'staging/' if staging else ''}{role}.yaml"]
        if (
            spec.get("destination") != expected_destination
            or source.get("repoURL") != "https://github.com/100-hours-a-week/KTB4-17th-CLOUD.git"
            or source.get("targetRevision") != "main"
            or source.get("path") != "manifest/helm/star23-service"
            or source.get("helm", {}).get("valueFiles") != expected_values
        ):
            raise ValueError(f"{name}: unexpected source, values or destination; check CLOUD environment wiring")
        if (
            status.get("sync", {}).get("comparedTo", {}).get("source") != source
            or status.get("sync", {}).get("comparedTo", {}).get("destination") != expected_destination
            or item["metadata"].get("deletionTimestamp")
            or status.get("sync", {}).get("status") != "Synced"
            or status.get("sync", {}).get("revision") != cloud_commit
            or status.get("health", {}).get("status") != "Healthy"
        ):
            raise RuntimeError(f"{name}: rollout is not Synced/Healthy at the required CLOUD commit")
    return {"status": "healthy", "environment": environment, "cloud_commit": cloud_commit, "applications": names}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-branch", required=True, choices=("dev", "main"))
    parser.add_argument("--cloud-commit", required=True)
    parser.add_argument("--argo-kubeconfig", required=True, type=Path)
    parser.add_argument("--argo-context", default=CONTROL_CONTEXT)
    args = parser.parse_args()
    environment = environment_for_branch(args.source_branch)
    validate_revision(args.cloud_commit, "sha256:" + "0" * 64)
    if args.argo_context != CONTROL_CONTEXT:
        raise ValueError("query Applications on the prod Argo control cluster, not the staging destination cluster")
    names = [f"staging-{role}" if environment == "staging" else role for role in ROLES]
    response = subprocess.run(
        [
            "kubectl",
            "--kubeconfig",
            str(args.argo_kubeconfig),
            "--context",
            args.argo_context,
            "get",
            "applications.argoproj.io",
            *names,
            "--namespace",
            "argocd",
            "--output",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    print(
        json.dumps(
            verify_applications(json.loads(response.stdout), branch=args.source_branch, cloud_commit=args.cloud_commit),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f"Rollout verification failed: {error}", file=sys.stderr)
        sys.exit(1)
