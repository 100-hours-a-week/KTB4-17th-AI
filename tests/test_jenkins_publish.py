from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/jenkins/push_image.sh"
DIGEST = "sha256:" + "a" * 64
IMAGE = "975779326240.dkr.ecr.ap-northeast-2.amazonaws.com/star23/ai"


def run_publish(tmp_path: Path, **overrides: str):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake = (
        f"#!{sys.executable}\n"
        + """import json, os, pathlib, sys
tool = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["CALLS"], "a") as output:
    output.write(json.dumps({"tool": tool, "args": args, "config": os.environ.get("DOCKER_CONFIG", "")}) + "\\n")
if tool == "aws":
    if args[:2] == ["sts", "get-caller-identity"]:
        print(os.environ.get("ACCOUNT", "975779326240"))
    elif args[:2] == ["ecr", "get-login-password"]:
        if os.environ.get("FAIL_LOGIN"):
            sys.exit(7)
        print("secret-token-never-log")
    elif args[:2] == ["ecr", "describe-images"]:
        calls = [json.loads(line) for line in pathlib.Path(os.environ["CALLS"]).read_text().splitlines()]
        lookups = sum(call["args"][:2] == ["ecr", "describe-images"] for call in calls)
        if lookups == 1:
            if os.environ.get("EXISTING_TAG"):
                print("sha256:" + "e" * 64)
                sys.exit(0)
            code = "AccessDeniedException" if os.environ.get("LOOKUP_FAILURE") else "ImageNotFoundException"
            print("An error occurred (" + code + ") when calling DescribeImages", file=sys.stderr)
            sys.exit(254)
        print(os.environ.get("DIGEST", "sha256:" + "a" * 64))
    else:
        sys.exit(99)
elif tool == "docker":
    if args[0] == "login":
        assert sys.stdin.read().strip() == "secret-token-never-log"
        pathlib.Path(os.environ["DOCKER_CONFIG"], "config.json").write_text("secret-token-never-log")
    elif args[0] in ("tag", "pull"):
        pass
    elif args[0] == "push":
        if os.environ.get("FAIL_PUSH"):
            sys.exit(8)
    elif args[:2] == ["image", "inspect"]:
        if args[-1] == "{{.Id}}":
            is_remote = "@" in args[2]
            letter = "f" if is_remote and os.environ.get("DIFFERENT_EXISTING_IMAGE") else "a"
            print("sha256:" + letter * 64)
        else:
            print(os.environ["IMAGE_REPO"] + "@" + os.environ.get("LOCAL_DIGEST", "sha256:" + "a" * 64))
    else:
        sys.exit(99)
"""
    )
    for name in ("aws", "docker"):
        command = fake_bin / name
        command.write_text(fake)
        command.chmod(0o755)
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "image-digest.txt").write_text("stale digest")
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CALLS": str(tmp_path / "calls.jsonl"),
        "CI_REPORT_DIR": str(reports),
        "AWS_REGION": "ap-northeast-2",
        "AWS_ACCOUNT_ID": "975779326240",
        "ECR_REPOSITORY": "star23/ai",
        "LOCAL_IMAGE": "star23-ai-build:test",
        "GIT_COMMIT": "b" * 40,
        "IMAGE_REPO": IMAGE,
        **overrides,
    }
    result = subprocess.run(["bash", str(SCRIPT)], env=env, text=True, capture_output=True)
    calls_file = tmp_path / "calls.jsonl"
    calls = [json.loads(line) for line in calls_file.read_text().splitlines()] if calls_file.exists() else []
    return result, reports, calls


def test_success_persists_verified_digest_and_removes_temporary_login(tmp_path: Path) -> None:
    result, reports, calls = run_publish(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (reports / "image-digest.txt").read_text().strip() == DIGEST
    assert (reports / "image-reference.txt").read_text().strip() == f"{IMAGE}@{DIGEST}"
    assert "secret-token-never-log" not in result.stdout + result.stderr
    logins = [call for call in calls if call["tool"] == "docker" and call["args"][0] == "login"]
    assert len(logins) == 1
    assert logins[0]["args"] == ["login", "--username", "AWS", "--password-stdin", IMAGE.split("/")[0]]
    assert not Path(logins[0]["config"]).exists()


@pytest.mark.parametrize(
    "overrides", [{"FAIL_LOGIN": "1"}, {"FAIL_PUSH": "1"}, {"DIGEST": "None"}, {"LOCAL_DIGEST": "sha256:" + "c" * 64}]
)
def test_failure_cannot_leave_a_digest_for_deployment(tmp_path: Path, overrides: dict[str, str]) -> None:
    result, reports, calls = run_publish(tmp_path, **overrides)
    assert result.returncode != 0
    assert not (reports / "image-digest.txt").exists()
    assert not (reports / "image-reference.txt").exists()
    for call in calls:
        if call["config"]:
            assert not Path(call["config"]).exists()


def test_wrong_aws_account_prevents_registry_login_and_push(tmp_path: Path) -> None:
    result, reports, calls = run_publish(tmp_path, ACCOUNT="000000000000")
    assert result.returncode != 0
    assert not (reports / "image-digest.txt").exists()
    assert all(call["tool"] != "docker" for call in calls)


@pytest.mark.parametrize("overrides", [{"EXISTING_TAG": "1", "DIFFERENT_EXISTING_IMAGE": "1"}, {"LOOKUP_FAILURE": "1"}])
def test_existing_different_image_or_lookup_error_cannot_overwrite(tmp_path: Path, overrides: dict[str, str]) -> None:
    result, reports, calls = run_publish(tmp_path, **overrides)
    assert result.returncode != 0
    assert not (reports / "image-digest.txt").exists()
    assert not any(call["tool"] == "docker" and call["args"][0] in ("tag", "push") for call in calls)


def test_identical_previously_published_image_is_reused_without_push(tmp_path: Path) -> None:
    result, reports, calls = run_publish(tmp_path, EXISTING_TAG="1", LOCAL_DIGEST="sha256:" + "e" * 64)
    assert result.returncode == 0, result.stderr
    assert (reports / "image-digest.txt").read_text().strip() == "sha256:" + "e" * 64
    assert any(call["tool"] == "docker" and call["args"][0] == "pull" for call in calls)
    assert not any(call["tool"] == "docker" and call["args"][0] in ("tag", "push") for call in calls)
