from __future__ import annotations

import json
import re

from ktb_git.errors import KtbError
from ktb_git.runner import Completed, Runner

GH_INSTALL_URL = "https://cli.github.com/"


def _gh_error(detail: str) -> KtbError:
    return KtbError(f"{detail}\nGitHub CLI 설치: {GH_INSTALL_URL}\n인증: gh auth login")


def _gh(runner: Runner, args: list[str], *, check: bool = True, dry_run: bool = False) -> Completed:
    try:
        return runner.run(["gh", *args], check=check, dry_run=dry_run)
    except FileNotFoundError as exc:
        raise _gh_error("gh를 찾을 수 없습니다.") from exc


def ensure_gh(runner: Runner) -> None:
    result = _gh(runner, ["auth", "status"], check=False)
    if result.returncode:
        raise _gh_error("GitHub CLI 인증이 필요합니다.")


def create_issue(runner: Runner, title: str, body: str, dry_run: bool = False) -> int:
    result = _gh(
        runner,
        ["issue", "create", "--title", title, "--body", body],
        dry_run=dry_run,
    )
    if dry_run:
        return 0
    match = re.search(r"/issues/(\d+)(?:\s|$)", result.stdout.strip())
    if not match:
        raise KtbError("생성된 GitHub 이슈 번호를 응답에서 찾을 수 없습니다.")
    return int(match.group(1))


def list_open_issues(runner: Runner, limit: int = 30) -> list[tuple[int, str]]:
    result = _gh(
        runner,
        [
            "issue",
            "list",
            "--state",
            "open",
            "--limit",
            str(limit),
            "--json",
            "number,title",
        ],
    )
    try:
        issues = json.loads(result.stdout)
        return [(int(issue["number"]), str(issue["title"])) for issue in issues]
    except (ValueError, TypeError, KeyError) as exc:
        raise KtbError("GitHub 이슈 목록을 해석할 수 없습니다.") from exc


def create_pr(runner: Runner, *, base: str, title: str, body: str, dry_run: bool = False) -> str:
    if base.startswith(("feat/", "fix/")):
        raise KtbError("기능 브랜치를 PR 대상 브랜치로 사용할 수 없습니다.")
    result = _gh(
        runner,
        ["pr", "create", "--base", base, "--title", title, "--body", body],
        dry_run=dry_run,
    )
    return "" if dry_run else result.stdout.strip()


def view_pr_for_branch(runner: Runner, branch: str) -> str | None:
    result = _gh(runner, ["pr", "view", branch, "--json", "url"], check=False)
    if result.returncode:
        if "no pull requests found" in result.stderr.lower() or "not found" in result.stderr.lower():
            return None
        raise KtbError(f"PR 조회에 실패했습니다: {result.stderr.strip() or result.stdout.strip()}")
    try:
        url = json.loads(result.stdout)["url"]
    except (ValueError, TypeError, KeyError) as exc:
        raise KtbError("GitHub PR 응답을 해석할 수 없습니다.") from exc
    return str(url) if url else None
