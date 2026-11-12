#!/usr/bin/env bash
# Runs inside Dockerfile.ci, with no production credentials or database service.
set -euo pipefail

reports=${CI_REPORT_DIR:-.ci-reports}
mkdir -p "$reports"
export LANGFUSE_TRACING_ENABLED=false
export DATABASE_URL=postgresql+asyncpg://ci:ci@127.0.0.1:5432/ktb_eval

case "${1:-}" in
  lint) uv run --no-sync ruff check . ;;
  format) uv run --no-sync ruff format --check . ;;
  security)
    # Audit exactly uv.lock, including test dependencies; never silently relock.
    uv export --all-groups --locked --no-emit-project --format requirements-txt > "$reports/requirements.txt"
    uvx pip-audit --no-deps --disable-pip --requirement "$reports/requirements.txt" \
      --format json --output "$reports/pip-audit.json"
    ;;
  unit) uv run --no-sync pytest --ignore-glob='tests/test_*_api.py' --junitxml="$reports/unit.xml" ;;
  api) uv run --no-sync pytest tests/test_*_api.py --junitxml="$reports/api.xml" ;;
  compile) uv run --no-sync python -m compileall -q -x '/(\.venv|\.git)/' . ;;
  *) echo 'Usage: check.sh {lint|format|security|unit|api|compile}' >&2; exit 64 ;;
esac
