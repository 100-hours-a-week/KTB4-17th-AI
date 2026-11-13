"""Read Argo Applications from the branch's own control cluster; never mutate either cluster."""

from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from scripts.jenkins.gitops_release import (
    GITOPS_REPOSITORY,
    IMAGE_REPOSITORY,
    ROLES,
    GitHub,
    environment_for_branch,
    inspect_values,
    read_values,
    validate_revision,
)
from scripts.jenkins.release_version import validate_image_tag

CONTROL_CONTEXT = "arn:aws:eks:ap-northeast-2:975779326240:cluster/star-EKS-v3"
STAGING_SERVER = "https://FD1D5E0955692E6EA1E41FADBE848111.gr7.ap-northeast-2.eks.amazonaws.com"


def context_for_branch(branch: str) -> str:
    environment = environment_for_branch(branch)
    return CONTROL_CONTEXT + ("-staging" if environment == "staging" else "")


def verify_applications(data: dict, *, branch: str, cloud_commit: str, expected_image: str | None = None) -> dict:
    environment = environment_for_branch(branch)
    validate_revision(cloud_commit, "sha256:" + "0" * 64)
    staging = environment == "staging"
    names = list(ROLES)
    items = data.get("items", [])
    if len(items) != 5 or {item.get("metadata", {}).get("name") for item in items} != set(names):
        raise ValueError("exactly the five environment-specific Argo Applications must be returned")
    for item in items:
        name = item["metadata"]["name"]
        role = name
        spec, status = item.get("spec", {}), item.get("status", {})
        expected_destination = {
            "server": "https://kubernetes.default.svc",
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
            item["metadata"].get("namespace") != "argocd"
            or spec.get("project") != "star23-apps"
            or spec.get("destination") != expected_destination
            or source.get("repoURL") != "https://github.com/100-hours-a-week/KTB4-17th-CLOUD.git"
            or source.get("targetRevision") != "main"
            or source.get("path") != "manifest/helm/star23-service"
            or source.get("helm", {}).get("valueFiles") != expected_values
        ):
            raise ValueError(f"{name}: unexpected source, values or destination; check CLOUD environment wiring")
        automated = spec.get("syncPolicy", {}).get("automated")
        if not isinstance(automated, dict) or automated.get("enabled") is False:
            raise ValueError(f"{name}: Argo auto-sync must be enabled")
        operation = status.get("operationState", {})
        if (
            operation.get("phase") in ("Failed", "Error")
            and operation.get("syncResult", {}).get("revision") == cloud_commit
        ) or any(
            condition.get("type") in ("InvalidSpecError", "ComparisonError", "SyncError")
            for condition in status.get("conditions", [])
        ):
            raise ValueError(f"{name}: Argo sync failed for the observed release")
        if (
            status.get("sync", {}).get("comparedTo", {}).get("source") != source
            or status.get("sync", {}).get("comparedTo", {}).get("destination") != expected_destination
            or item["metadata"].get("deletionTimestamp")
            or status.get("sync", {}).get("status") != "Synced"
            or status.get("sync", {}).get("revision") != cloud_commit
            or status.get("health", {}).get("status") != "Healthy"
        ):
            raise RuntimeError(f"{name}: rollout is not Synced/Healthy at the required CLOUD commit")
        if expected_image is not None:
            images = {
                value
                for value in status.get("summary", {}).get("images", [])
                if value.startswith((IMAGE_REPOSITORY + "@", IMAGE_REPOSITORY + ":"))
            }
            if images != {expected_image} or status.get("operationState", {}).get("phase") in (
                "Running",
                "Terminating",
            ):
                raise RuntimeError(f"{name}: the expected image rollout has not completed")
    return {"status": "healthy", "environment": environment, "cloud_commit": cloud_commit, "applications": names}


def aws_json(*arguments):
    try:
        result = subprocess.run(
            ["aws", "--region", "ap-northeast-2", "--no-cli-pager", *arguments, "--output", "json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise ValueError("AWS read failed; check the Jenkins instance profile and EKS observer access") from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class EKSApplications:
    def __init__(self, branch):
        self.context = context_for_branch(branch)
        self.cluster = self.context.rsplit("/", 1)[1]
        cluster = aws_json("eks", "describe-cluster", "--name", self.cluster)["cluster"]
        if cluster.get("arn") != self.context or not cluster.get("endpoint", "").startswith("https://"):
            raise ValueError("Unexpected Argo control cluster identity")
        self.endpoint = cluster["endpoint"]
        context = ssl.create_default_context(cadata=base64.b64decode(cluster["certificateAuthority"]["data"]).decode())
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))

    def applications(self):
        token = aws_json("eks", "get-token", "--cluster-name", self.cluster)["status"]["token"]
        items = []
        for name in ROLES:
            path = "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/" + name
            request = urllib.request.Request(
                self.endpoint + path, headers={"Authorization": "Bearer " + token, "Accept": "application/json"}
            )
            try:
                with self.opener.open(request, timeout=15) as response:
                    items.append(json.load(response))
            except urllib.error.HTTPError as error:
                if error.code in (429, 500, 502, 503, 504):
                    raise RuntimeError(f"Temporary EKS Application read failure: HTTP {error.code}") from None
                raise ValueError(f"EKS Application read denied or unavailable: HTTP {error.code}") from None
            except (urllib.error.URLError, TimeoutError):
                raise RuntimeError("Temporary EKS Application transport failure") from None
        return {"items": items}


class ReadOnlyGitHub(GitHub):
    def request(self, method, endpoint, payload=None, **kwargs):
        if method != "GET":
            raise ValueError("Rollout observation is read-only")
        return super().request(method, endpoint, payload, **kwargs)


def wait_for_release(
    reader, cloud, release, *, branch, timeout=900, interval=10, clock=time.monotonic, sleep=time.sleep, progress=None
):
    environment = environment_for_branch(branch)
    commit, digest = release["cloud_commit"], release["digest"]
    validate_revision(commit, digest)
    validate_revision(release["source_sha"], digest)
    validate_image_tag(release["image_tag"], release["source_sha"], branch)
    image = IMAGE_REPOSITORY + "@" + digest
    expected_cluster = context_for_branch(branch).rsplit("/", 1)[1]
    if (
        cloud.repository != GITOPS_REPOSITORY
        or release.get("gitops_repository") != GITOPS_REPOSITORY
        or release.get("base_branch") != "main"
        or release.get("environment") != environment
        or release.get("cluster") != expected_cluster
        or release.get("applications") != list(ROLES)
        or release.get("image") != image
        or timeout <= 0
        or interval <= 0
    ):
        raise ValueError("Unexpected environment-specific deployment receipt")
    checked = set()
    deadline = clock() + timeout

    def verify_revision(revision):
        validate_revision(revision, digest)
        if revision in checked:
            return True
        if revision != commit:
            relationship = cloud.request("GET", f"compare/{commit}...{revision}")["status"]
            if relationship == "behind":
                return False
            if relationship not in ("ahead", "identical"):
                raise ValueError("CLOUD revision diverged from the published release")
        changes, _ = inspect_values(
            read_values(cloud, revision, environment), digest, environment, image_tag=release["image_tag"]
        )
        if changes:
            raise ValueError("AI release was superseded by a different tag or digest")
        checked.add(revision)
        return True

    while True:
        head = cloud.request("GET", "git/ref/heads/main")["object"]["sha"]
        if not verify_revision(head):
            raise ValueError("CLOUD/main no longer contains the published release")
        pending = None
        observed = None
        try:
            data = reader.applications()
            revisions = {item.get("status", {}).get("sync", {}).get("revision", "") for item in data.get("items", [])}
            if len(revisions) != 1 or not all(revisions):
                raise RuntimeError("Applications have not reached a common CLOUD revision")
            observed = next(iter(revisions))
            if not verify_revision(observed):
                raise RuntimeError("Applications still observe an older CLOUD revision")
            verify_applications(data, branch=branch, cloud_commit=observed, expected_image=image)
            latest = cloud.request("GET", "git/ref/heads/main")["object"]["sha"]
            if not verify_revision(latest):
                raise ValueError("CLOUD/main no longer contains the published release")
        except RuntimeError as error:
            pending = str(error)
        report = {
            "status": "waiting-for-argocd" if pending else "deployed",
            "environment": environment,
            "cluster": expected_cluster,
            "cloud_commit": commit,
            "observed_revision": observed,
            "image": image,
            "image_tag": release["image_tag"],
            "applications": list(ROLES),
        }
        if pending:
            report["pending_reason"] = pending
        if progress:
            progress(report)
        if not pending:
            return report
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError("Argo rollout timed out: " + pending)
        sleep(min(interval, remaining))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-branch", required=True, choices=("dev", "main"))
    parser.add_argument("--cloud-commit")
    parser.add_argument("--argo-kubeconfig", type=Path)
    parser.add_argument("--argo-context")
    parser.add_argument("--deployment-file", type=Path)
    parser.add_argument("--output-file", type=Path, default=Path(".ci-reports/argocd-status.json"))
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    if args.deployment_file:
        release = json.loads(args.deployment_file.read_text())

        def record(report):
            args.output_file.parent.mkdir(parents=True, exist_ok=True)
            args.output_file.write_text(json.dumps(report, indent=2) + "\n")

        result = wait_for_release(
            EKSApplications(args.source_branch),
            ReadOnlyGitHub(os.environ.get("CLOUD_READ_TOKEN", "")),
            release,
            branch=args.source_branch,
            timeout=args.timeout_seconds,
            progress=record,
        )
        print(json.dumps(result, indent=2))
        return 0
    if not args.cloud_commit or not args.argo_kubeconfig:
        raise ValueError("Provide --deployment-file or both --cloud-commit and --argo-kubeconfig")
    validate_revision(args.cloud_commit, "sha256:" + "0" * 64)
    context = context_for_branch(args.source_branch)
    if args.argo_context and args.argo_context != context:
        raise ValueError("Argo control context must match the trusted source branch")
    names = list(ROLES)
    response = subprocess.run(
        [
            "kubectl",
            "--kubeconfig",
            str(args.argo_kubeconfig),
            "--context",
            context,
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
