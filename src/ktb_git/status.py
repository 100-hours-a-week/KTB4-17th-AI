from __future__ import annotations

from dataclasses import dataclass

from ktb_git import config, conventions, github, gitops
from ktb_git.errors import KtbError
from ktb_git.runner import CommandError, Runner


@dataclass
class Snapshot:
    branch: str | None
    linked_issue: int | None
    dirty_count: int
    ahead_count: int | None
    pr_url: str | None
    next_action: str
    github_error: str | None = None

    @property
    def can_commit(self) -> bool:
        return self.dirty_count > 0 and self.branch != "main"

    @property
    def can_ship(self) -> bool:
        return bool(
            self.branch
            and conventions.is_workflow_branch(self.branch)
            and self.ahead_count is not None
            and self.ahead_count > 0
        )


def next_action(branch: str | None, dirty_count: int, ahead_count: int | None, pr_url: str | None) -> str:
    if branch == "main" or not branch or not conventions.is_workflow_branch(branch):
        return "다음: ktb start로 작업 브랜치를 시작하세요."
    if dirty_count:
        return "다음: ktb commit으로 변경 사항을 커밋하세요."
    if ahead_count and not pr_url:
        return "다음: ktb ship으로 Push하고 PR을 여세요."
    if pr_url:
        return "다음: 열린 PR을 확인하세요."
    return "다음: 변경 사항을 만든 뒤 ktb commit을 실행하세요."


def snapshot(runner: Runner) -> Snapshot:
    root = gitops.repo_root(runner)
    project = config.load_project_config(root)
    branch = None if gitops.is_detached(runner) else gitops.current_branch(runner)
    dirty_count = len(gitops.dirty_files(runner))
    issue = gitops.get_branch_issue(runner, branch) if branch else None
    ahead_count: int | None = None
    pr_url = None
    github_error = None
    if branch and conventions.is_workflow_branch(branch):
        base = conventions.pr_base_for_branch(branch, project.default_base, project.hotfix_base)
        try:
            ahead_count = gitops.commits_ahead(runner, f"origin/{base}")
        except (CommandError, KtbError):
            ahead_count = None
        try:
            pr_url = github.view_pr_for_branch(runner, branch)
        except (CommandError, KtbError) as exc:
            github_error = str(exc)
    return Snapshot(
        branch=branch,
        linked_issue=issue,
        dirty_count=dirty_count,
        ahead_count=ahead_count,
        pr_url=pr_url,
        next_action=next_action(branch, dirty_count, ahead_count, pr_url),
        github_error=github_error,
    )


def render(state: Snapshot) -> None:
    print(f"브랜치: {state.branch or '분리된 HEAD'}")
    print(f"연결 이슈: #{state.linked_issue}" if state.linked_issue else "연결 이슈: 없음")
    print(f"변경 파일: {state.dirty_count}개")
    print(f"기준 브랜치보다 앞선 커밋: {state.ahead_count if state.ahead_count is not None else '조회 불가'}")
    print(f"PR: {state.pr_url or '없음'}")
    if state.github_error:
        print(f"GitHub 상태: {state.github_error}")
    print(state.next_action)


def run(runner: Runner) -> Snapshot:
    state = snapshot(runner)
    render(state)
    return state
