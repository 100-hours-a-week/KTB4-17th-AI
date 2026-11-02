"""Execute the DEV deploy shell against fake SSH/ECR/Compose boundaries."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import yaml


def test_each_deployment_refreshes_langfuse_before_migration_and_start(tmp_path):
    app = tmp_path / "app"
    (app / "ai").mkdir(parents=True)
    (app / ".env").write_text("AI_IMAGE=old\nOTHER_SETTING=preserved\n")
    (app / "ai/.env").write_text("OTHER_SETTING=preserved\nLANGFUSE_RELEASE=old\nLANGFUSE_TRACING_ENVIRONMENT=default")
    commands = tmp_path / "bin"
    commands.mkdir()
    log = tmp_path / "compose.jsonl"
    scripts = {
        "ssh": f"import subprocess,sys\nsubprocess.run(sys.argv[-1].replace('cd /home/ubuntu/app', 'cd {shlex.quote(str(app))}'), shell=True, executable='/bin/bash', check=True)\n",
        "aws": "print('synthetic-login')\n",
        "docker": f"import json,sys\nfrom pathlib import Path\nwith open({str(log)!r},'a') as f: f.write(json.dumps({{'args':sys.argv[1:], 'env':Path('ai/.env').read_text(), 'image':Path('.env').read_text()}})+'\\n')\n",
    }
    if sys.platform == "darwin":
        # DEV runs GNU sed; translate its in-place flag for the real BSD sed locally.
        scripts["sed"] = "import os,sys\nos.execv('/usr/bin/sed', ['sed','-i','',*sys.argv[2:]])\n"
    for name, body in scripts.items():
        script = commands / name
        script.write_text(f"#!{sys.executable}\n{body}")
        script.chmod(0o755)
    workflow = yaml.safe_load((Path(__file__).parents[1] / ".github/workflows/dev-cd.yml").read_text())
    deploy = next(step["run"] for step in workflow["jobs"]["deploy"]["steps"] if step["name"] == "Deploy to DEV EC2")
    for sha in ["a" * 40, "b" * 40]:
        subprocess.run(
            ["bash", "-eu", "-c", deploy],
            check=True,
            env={
                **os.environ,
                "PATH": f"{commands}:{os.environ['PATH']}",
                "RUNNER_TEMP": str(tmp_path),
                "EC2_USER": "test",
                "EC2_HOST": "dev.test",
                "EC2_PORT": "22",
                "AWS_REGION": "ap-northeast-2",
                "ECR_REGISTRY": "registry.test",
                "ECR_REPOSITORY": "ai",
                "IMAGE_TAG": sha,
            },
            capture_output=True,
            text=True,
        )
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        migration, startup = rows[-2:]
        assert migration["args"][:2] == ["compose", "run"]
        assert startup["args"] == ["compose", "up", "-d", "--no-deps", "ai"]
        for row in [migration, startup]:
            env = row["env"].splitlines()
            assert env.count("LANGFUSE_TRACING_ENVIRONMENT=development") == 1
            assert env.count(f"LANGFUSE_RELEASE={sha}") == 1
            assert sum(line.startswith("LANGFUSE_RELEASE=") for line in env) == 1
            assert "OTHER_SETTING=preserved" in env
            assert f"AI_IMAGE=registry.test/ai:{sha}" in row["image"].splitlines()
