from __future__ import annotations

from pathlib import Path

from ktb_git import config, conventions, github, gitops
from ktb_git.commands.init import ensure_user_config
from ktb_git.errors import KtbError
from ktb_git.runner import Runner
from ktb_git.wizard import Choice, Wizard


def _issue_type(wizard: Wizard) -> str:
    return str(
        wizard.select(
            "issue_type",
            "이슈 분류를 선택하세요:",
            [Choice(kind, kind) for kind in conventions.ISSUE_TYPES],
            default="feat",
        )
    )


def _feature_slug(wizard: Wizard, summary: str) -> str:
    suggested = conventions.slugify_feature(summary)
    while True:
        slug = wizard.text("feature_slug", "기능명 slug(영문·숫자·하이픈)를 입력하세요:", default=suggested).strip()
        try:
            return conventions.validate_feature_slug(slug)
        except conventions.ConventionError as exc:
            print(exc)


def run(
    runner: Runner,
    wizard: Wizard,
    *,
    dry_run: bool = False,
    config_path: Path | None = None,
) -> str:
    root = gitops.repo_root(runner)
    current = gitops.current_branch(runner)
    if conventions.is_workflow_branch(current):
        print(f"현재 작업 브랜치({current})에서 새 브랜치를 시작합니다.")
    if gitops.dirty_files(runner):
        print("변경 파일이 있습니다. 브랜치 전환이 충돌하면 중단될 수 있습니다.")
    if not dry_run:
        github.ensure_gh(runner)
    user = ensure_user_config(wizard, config_path=config_path, dry_run=dry_run)
    project = config.load_project_config(root)

    mode = wizard.select(
        "issue_mode",
        "새 이슈를 만들까요, 기존 이슈를 선택할까요?",
        [Choice("새 이슈 만들기", "new"), Choice("기존 이슈 선택", "existing")],
        default="new",
    )
    issue_number: int
    if mode == "new":
        issue_type = _issue_type(wizard)
        assignee = wizard.text("assignee", "담당자 닉네임:", default=user.nickname).strip()
        while not assignee:
            print("담당자는 비울 수 없습니다.")
            assignee = wizard.text("assignee", "담당자 닉네임:", default=user.nickname).strip()
        while True:
            summary = wizard.text("issue_summary", "이슈 작업 내용:").strip()
            try:
                title = conventions.issue_title(issue_type, assignee, summary)
                break
            except conventions.ConventionError as exc:
                print(exc)
        body = conventions.issue_body(issue_type, assignee)
        print(f"이슈 미리보기: {title}\n{body}")
        issue_number = github.create_issue(runner, title, body, dry_run=dry_run)
    else:
        issues = github.list_open_issues(runner)
        if not issues:
            raise KtbError("열린 이슈가 없습니다. 새 이슈를 만들어 주세요.")
        issue_number = int(
            wizard.select(
                "existing_issue",
                "작업할 이슈를 선택하세요:",
                [Choice(f"#{number} {title}", number) for number, title in issues],
            )
        )
        summary = next(title for number, title in issues if number == issue_number)
        issue_type = conventions.parse_issue_type_from_title(summary) or _issue_type(wizard)

    suggested_kind = conventions.branch_kind_from_issue_type(issue_type)
    kind = str(
        wizard.select(
            "branch_kind",
            "브랜치 종류를 선택하세요:",
            [Choice(value, value) for value in conventions.BRANCH_KINDS],
            default=suggested_kind,
        )
    )
    slug = _feature_slug(wizard, summary)
    branch = conventions.branch_name(kind, user.nickname_slug, slug)
    base_ref = conventions.fork_ref(kind, project.default_base, project.hotfix_base)
    print(f"브랜치 미리보기: {branch} (기준: {base_ref})")
    if not wizard.confirm("start_confirm", "이슈와 브랜치를 시작할까요?", default=True):
        raise KtbError("브랜치 시작을 취소했습니다.")

    gitops.fetch_origin(runner, dry_run=dry_run)
    gitops.checkout(runner, base_ref, dry_run=dry_run)
    gitops.create_branch(runner, branch, base_ref, dry_run=dry_run)
    if issue_number:
        gitops.set_branch_issue(runner, branch, issue_number, dry_run=dry_run)
    elif dry_run:
        print(f"git config branch.{branch}.ktbIssue <생성된 이슈 번호>")
    print(f"다음: 작업 후 ktb commit, 그다음 ktb ship을 실행하세요. ({branch})")
    return branch
