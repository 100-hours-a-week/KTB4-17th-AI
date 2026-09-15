from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TypeVar

import typer

from ktb_git import hub, status
from ktb_git.commands import commit, init, ship, start
from ktb_git.conventions import ConventionError
from ktb_git.errors import KtbError
from ktb_git.runner import CommandError, SubprocessRunner
from ktb_git.wizard import QuestionaryWizard

app = typer.Typer(
    no_args_is_help=False,
    add_completion=False,
    help="KTB Git 규칙을 따라 이슈, 브랜치, 커밋, PR을 만드는 도구",
)
T = TypeVar("T")


def _invoke(operation: Callable[[], T]) -> T:
    try:
        return operation()
    except KtbError as exc:
        print(str(exc), file=sys.stderr)
        raise typer.Exit(code=exc.exit_code) from exc
    except (ConventionError, CommandError) as exc:
        print(str(exc), file=sys.stderr)
        raise typer.Exit(code=1) from exc


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _invoke(lambda: hub.run(SubprocessRunner(), QuestionaryWizard()))


@app.command("status")
def status_command() -> None:
    """현재 브랜치, 이슈, 변경 파일, PR을 표시합니다."""
    _invoke(lambda: status.run(SubprocessRunner()))


@app.command("init")
def init_command() -> None:
    """닉네임을 저장하고 GitHub CLI 인증을 확인합니다."""
    _invoke(lambda: init.run(SubprocessRunner(), QuestionaryWizard()))


@app.command("start")
def start_command(dry_run: bool = typer.Option(False, "--dry-run", help="명령만 미리 봅니다")) -> None:
    """이슈를 만들거나 선택하고 작업 브랜치를 시작합니다."""
    _invoke(lambda: start.run(SubprocessRunner(), QuestionaryWizard(), dry_run=dry_run))


@app.command("commit")
def commit_command(
    dry_run: bool = typer.Option(False, "--dry-run", help="명령만 미리 봅니다"),
    skip_ruff: bool = typer.Option(False, "--skip-ruff", help="Ruff 실행을 건너뜁니다"),
) -> None:
    """변경 파일을 스테이징하고 Conventional Commit을 만듭니다."""
    _invoke(lambda: commit.run(SubprocessRunner(), QuestionaryWizard(), dry_run=dry_run, skip_ruff=skip_ruff))


@app.command("ship")
def ship_command(
    dry_run: bool = typer.Option(False, "--dry-run", help="명령만 미리 봅니다"),
    skip_ruff: bool = typer.Option(False, "--skip-ruff", help="Ruff 실행을 건너뜁니다"),
) -> None:
    """현재 브랜치를 Push하고 PR을 엽니다."""
    _invoke(lambda: ship.run(SubprocessRunner(), QuestionaryWizard(), dry_run=dry_run, skip_ruff=skip_ruff))
