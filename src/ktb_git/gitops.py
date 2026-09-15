from __future__ import annotations

import tempfile
from pathlib import Path

from ktb_git.errors import KtbError
from ktb_git.runner import Completed, Runner


def _git(runner: Runner, args: list[str], *, check: bool = True, dry_run: bool = False) -> Completed:
    try:
        return runner.run(["git", *args], check=check, dry_run=dry_run)
    except FileNotFoundError as exc:
        raise KtbError("git을 찾을 수 없습니다. git을 설치한 뒤 다시 시도하세요.") from exc


def repo_root(runner: Runner) -> Path:
    result = _git(runner, ["rev-parse", "--show-toplevel"], check=False)
    if result.returncode or not result.stdout.strip():
        raise KtbError("Git 저장소 안에서 ktb를 실행하세요.")
    return Path(result.stdout.strip())


def is_detached(runner: Runner) -> bool:
    result = _git(runner, ["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
    return result.returncode != 0 or not result.stdout.strip()


def current_branch(runner: Runner) -> str:
    result = _git(runner, ["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
    if result.returncode or not result.stdout.strip():
        raise KtbError("현재 HEAD가 브랜치를 가리키지 않습니다. 브랜치를 체크아웃하세요.")
    return result.stdout.strip()


def name_only_paths(runner: Runner, *, staged: bool) -> list[str]:
    args = ["diff", "--cached", "--name-only", "-z"] if staged else ["diff", "--name-only", "-z"]
    result = _git(runner, args)
    return [path for path in result.stdout.split("\0") if path]


def dirty_files(runner: Runner) -> list[str]:
    result = _git(runner, ["status", "--porcelain=v1", "-z"])
    records = result.stdout.split("\0")
    files: list[str] = []
    index = 0
    while index < len(records):
        record = records[index]
        if not record:
            index += 1
            continue
        if len(record) < 4:
            raise KtbError("git status 결과를 해석할 수 없습니다.")
        files.append(record[3:])
        if "R" in record[:2] or "C" in record[:2]:
            index += 1  # porcelain -z provides the old path as a second record
        index += 1
    return files


def stage_paths(runner: Runner, paths: list[str], *, dry_run: bool = False) -> None:
    if paths:
        _git(runner, ["add", "--", *paths], dry_run=dry_run)


def commit(runner: Runner, message: str, *, dry_run: bool = False) -> None:
    if not message.strip():
        raise KtbError("커밋 메시지를 입력하세요.")
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix="ktb-commit-", suffix=".txt", delete=False
    ) as message_file:
        message_file.write(message)
        message_path = Path(message_file.name)
    try:
        _git(runner, ["commit", "-F", str(message_path)], dry_run=dry_run)
    finally:
        message_path.unlink(missing_ok=True)


def fetch_origin(runner: Runner, *, dry_run: bool = False) -> None:
    _git(runner, ["fetch", "origin"], dry_run=dry_run)


def checkout(runner: Runner, ref: str, *, dry_run: bool = False) -> None:
    _git(runner, ["checkout", ref], dry_run=dry_run)


def create_branch(runner: Runner, name: str, from_ref: str, *, dry_run: bool = False) -> None:
    _git(runner, ["checkout", "-b", name, from_ref], dry_run=dry_run)


def push(runner: Runner, *, dry_run: bool = False) -> None:
    _git(runner, ["push", "-u", "origin", "HEAD"], dry_run=dry_run)


def commits_ahead(runner: Runner, base: str) -> int:
    result = _git(runner, ["rev-list", "--count", f"{base}..HEAD"])
    try:
        return int(result.stdout.strip())
    except ValueError as exc:
        raise KtbError("기준 브랜치보다 앞선 커밋 수를 읽을 수 없습니다.") from exc


def last_commit_subject(runner: Runner) -> str:
    return _git(runner, ["log", "-1", "--pretty=%s"]).stdout.strip()


def commit_messages_since(runner: Runner, base: str) -> list[str]:
    result = _git(runner, ["log", f"{base}..HEAD", "--format=%B%x00"])
    return [message.strip() for message in result.stdout.split("\0") if message.strip()]


def set_branch_issue(runner: Runner, branch: str, issue_number: int, *, dry_run: bool = False) -> None:
    if issue_number < 1:
        raise KtbError("이슈 번호는 양수여야 합니다.")
    _git(
        runner,
        ["config", f"branch.{branch}.ktbIssue", str(issue_number)],
        dry_run=dry_run,
    )


def get_branch_issue(runner: Runner, branch: str) -> int | None:
    result = _git(runner, ["config", f"branch.{branch}.ktbIssue"], check=False)
    if result.returncode or not result.stdout.strip():
        return None
    try:
        return int(result.stdout.strip())
    except ValueError as exc:
        raise KtbError("브랜치에 연결된 이슈 번호가 올바르지 않습니다.") from exc
