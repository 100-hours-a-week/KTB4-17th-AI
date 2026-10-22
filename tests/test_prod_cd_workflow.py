from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SCRIPT = ROOT / "scripts" / "run_ecs_migration.sh"
PROD_CD_WORKFLOW = ROOT / ".github" / "workflows" / "prod-cd.yml"


def _write_fixture_files(tmp_path: Path) -> tuple[Path, Path]:
    rendered = tmp_path / "task-definition.json"
    rendered.write_text(
        json.dumps(
            {
                "family": "star23-ai",
                "taskDefinitionArn": "arn:aws:ecs:region:account:task-definition/star23-ai:8",
                "revision": 8,
                "status": "ACTIVE",
                "requiresAttributes": [],
                "compatibilities": ["EC2", "FARGATE"],
                "registeredAt": "2026-10-22T00:00:00Z",
                "registeredBy": "arn:aws:iam::account:role/deployer",
                "networkMode": "awsvpc",
                "requiresCompatibilities": ["FARGATE"],
                "cpu": "1024",
                "memory": "2048",
                "containerDefinitions": [
                    {
                        "name": "ai",
                        "image": "account.dkr.ecr.region.amazonaws.com/star23/ai@sha256:new",
                        "entryPoint": ["/code/.venv/bin/uvicorn"],
                        "command": ["app.main:app"],
                        "secrets": [{"name": "DATABASE_URL", "valueFrom": "secret-arn"}],
                        "logConfiguration": {"logDriver": "awslogs"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    service = tmp_path / "service.json"
    service.write_text(
        json.dumps(
            {
                "services": [
                    {
                        "networkConfiguration": {
                            "awsvpcConfiguration": {
                                "subnets": ["subnet-a", "subnet-b"],
                                "securityGroups": ["sg-ai"],
                                "assignPublicIp": "DISABLED",
                            }
                        }
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return rendered, service


def _write_fake_aws(tmp_path: Path) -> tuple[Path, Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log = tmp_path / "aws-calls.jsonl"
    registered_definition = tmp_path / "registered-definition.json"
    aws = fake_bin / "aws"
    aws.write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' \"$*\" >> \"$AWS_CALL_LOG\"

if [ \"$1 $2\" = \"ecs register-task-definition\" ]; then
    shift 2
    while [ \"$#\" -gt 0 ]; do
        case \"$1\" in
            --cli-input-json)
                cp \"${2#file://}\" \"$REGISTERED_DEFINITION\"
                shift 2
                ;;
            *) shift ;;
        esac
    done
    printf '%s\\n' 'arn:aws:ecs:region:account:task-definition/star23-ai-migration:9'
elif [ \"$1 $2\" = \"ecs run-task\" ]; then
    if [ \"${RUN_TASK_FAILURE:-0}\" = \"1\" ]; then
        printf '%s\\n' '{\"failures\":[{\"arn\":\"star23-ai-migration\",\"reason\":\"RESOURCE:CPU\"}],\"tasks\":[]}'
    else
        printf '%s\\n' '{\"failures\":[],\"tasks\":[{\"taskArn\":\"arn:aws:ecs:region:account:task/cluster/migration-task\"}]}'
    fi
elif [ \"$1 $2 $3\" = \"ecs wait tasks-stopped\" ]; then
    exit 0
elif [ \"$1 $2\" = \"ecs describe-tasks\" ]; then
    printf '{\"failures\":[],\"tasks\":[{\"stopCode\":\"EssentialContainerExited\",\"stoppedReason\":\"Essential container in task exited\",\"containers\":[{\"name\":\"ai\",\"exitCode\":%s,\"reason\":null}]}]}\\n' \"${MIGRATION_EXIT_CODE:-0}\"
else
    printf '%s\\n' \"unexpected aws call: $*\" >&2
    exit 64
fi
""",
        encoding="utf-8",
    )
    aws.chmod(0o755)
    return fake_bin, call_log, registered_definition


def _run_migration(
    tmp_path: Path, *, exit_code: int = 0, run_task_failure: bool = False
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    rendered, service = _write_fixture_files(tmp_path)
    fake_bin, call_log, registered_definition = _write_fake_aws(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "AWS_CALL_LOG": str(call_log),
            "REGISTERED_DEFINITION": str(registered_definition),
            "MIGRATION_EXIT_CODE": str(exit_code),
            "RUN_TASK_FAILURE": "1" if run_task_failure else "0",
        }
    )
    result = subprocess.run(
        [
            "bash",
            str(MIGRATION_SCRIPT),
            str(rendered),
            str(service),
            "star23-ecs-cluster",
            "ai",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, call_log, registered_definition


def test_migration_task_reuses_rendered_definition_and_service_network(tmp_path: Path) -> None:
    result, call_log, registered_definition = _run_migration(tmp_path)

    assert result.returncode == 0, result.stderr
    definition = json.loads(registered_definition.read_text(encoding="utf-8"))
    container = definition["containerDefinitions"][0]
    assert definition["family"] == "star23-ai-migration"
    assert "taskDefinitionArn" not in definition
    assert "revision" not in definition
    assert container["image"].endswith("@sha256:new")
    assert container["secrets"] == [{"name": "DATABASE_URL", "valueFrom": "secret-arn"}]
    assert container["logConfiguration"] == {"logDriver": "awslogs"}
    assert container["entryPoint"] == ["/code/.venv/bin/alembic"]
    assert container["command"] == ["upgrade", "head"]

    calls = call_log.read_text(encoding="utf-8")
    assert "ecs run-task" in calls
    assert "--cluster star23-ecs-cluster" in calls
    assert '"subnets":["subnet-a","subnet-b"]' in calls
    assert '"securityGroups":["sg-ai"]' in calls
    assert '"assignPublicIp":"DISABLED"' in calls
    assert "ecs wait tasks-stopped" in calls
    assert "ecs describe-tasks" in calls


def test_migration_task_failure_returns_nonzero_and_blocks_deploy(tmp_path: Path) -> None:
    result, _, _ = _run_migration(tmp_path, exit_code=1)

    assert result.returncode != 0
    assert '"exitCode": 1' in result.stdout


def test_run_task_api_failure_is_reported_and_blocks_deploy(tmp_path: Path) -> None:
    result, _, _ = _run_migration(tmp_path, run_task_failure=True)

    assert result.returncode != 0
    assert "RESOURCE:CPU" in result.stdout


def test_prod_cd_wires_migration_before_deploy_without_empty_steps() -> None:
    workflow = PROD_CD_WORKFLOW.read_text(encoding="utf-8")

    assert "\n            -\n" not in workflow
    migration_step = workflow.index("- name: Run database migrations")
    deploy_step = workflow.index("- name: Deploy AI")
    assert "bash scripts/run_ecs_migration.sh" in workflow
    assert migration_step < deploy_step
