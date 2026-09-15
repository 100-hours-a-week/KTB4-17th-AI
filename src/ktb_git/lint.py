from __future__ import annotations

import shutil

from ktb_git.errors import KtbError
from ktb_git.runner import Runner

UV_INSTALL_URL = "https://docs.astral.sh/uv/getting-started/installation/"


def ensure_uv() -> None:
    if shutil.which("uv") is None:
        raise KtbError(f"uv가 설치되지 않았습니다. 설치 안내: {UV_INSTALL_URL}")


def _run_ruff(runner: Runner, args: list[str], *, dry_run: bool) -> None:
    try:
        runner.run(args, dry_run=dry_run)
    except FileNotFoundError as exc:
        raise KtbError(f"uv가 설치되지 않았습니다. 설치 안내: {UV_INSTALL_URL}") from exc


def ruff_fix(runner: Runner, *, dry_run: bool = False) -> None:
    if not dry_run:
        ensure_uv()
    _run_ruff(runner, ["uv", "run", "ruff", "check", "--fix", "."], dry_run=dry_run)
    _run_ruff(runner, ["uv", "run", "ruff", "format", "."], dry_run=dry_run)


def ruff_check(runner: Runner, *, dry_run: bool = False) -> None:
    if not dry_run:
        ensure_uv()
    _run_ruff(runner, ["uv", "run", "ruff", "format", "--check", "."], dry_run=dry_run)
    _run_ruff(runner, ["uv", "run", "ruff", "check", "."], dry_run=dry_run)
