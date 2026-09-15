from __future__ import annotations

from ktb_git import config, conventions, github, gitops, lint
from ktb_git.errors import KtbError
from ktb_git.runner import CommandError, Runner
from ktb_git.wizard import Wizard

_FIX_HINT = "uv run ruff check --fix . && uv run ruff format ."


def _issue_number(runner: Runner, wizard: Wizard, branch: str) -> int:
    linked = gitops.get_branch_issue(runner, branch)
    if linked:
        return linked
    while True:
        entered = wizard.text("pr_issue_number", "PR에 연결할 이슈 번호:").strip().lstrip("#")
        try:
            number = int(entered)
            if number > 0:
                return number
        except ValueError:
            pass
        print("양수 이슈 번호를 입력하세요.")


def _changes(wizard: Wizard) -> list[str]:
    while True:
        entered = wizard.text("pr_changes", "변경 내용을 쉼표로 구분해 입력하세요:")
        changes = [item.strip() for item in entered.replace("\n", ",").split(",") if item.strip()]
        if changes:
            return changes
        print("변경 내용은 한 줄 이상 필요합니다.")


def run(
    runner: Runner,
    wizard: Wizard,
    *,
    dry_run: bool = False,
    skip_ruff: bool = False,
) -> str:
    root = gitops.repo_root(runner)
    branch = gitops.current_branch(runner)
    if conventions.is_protected_branch(branch):
        raise KtbError("main/dev 브랜치는 직접 Push하거나 PR을 열 수 없습니다. ktb start를 실행하세요.")
    if not conventions.is_workflow_branch(branch):
        raise KtbError("feat/, fix/, hotfix/ 브랜치에서만 ktb ship을 실행할 수 있습니다.")
    project = config.load_project_config(root)
    base = conventions.pr_base_for_branch(branch, project.default_base, project.hotfix_base)
    if base.startswith(("feat/", "fix/")):
        raise KtbError("기능 브랜치를 PR 대상 브랜치로 사용할 수 없습니다.")
    origin_base = f"origin/{base}"
    if gitops.commits_ahead(runner, origin_base) < 1:
        raise KtbError(f"{origin_base}보다 앞선 커밋이 없습니다. 먼저 ktb commit을 실행하세요.")
    if not dry_run:
        github.ensure_gh(runner)

    if skip_ruff:
        print("경고: --skip-ruff로 Ruff 검사를 건너뜁니다.")
    else:
        try:
            lint.ruff_check(runner, dry_run=dry_run)
        except CommandError as exc:
            raise KtbError(f"Ruff 검사에 실패해 Push와 PR 생성을 중단합니다.\n{exc}\n수정: {_FIX_HINT}") from exc

    gitops.push(runner, dry_run=dry_run)
    if not dry_run:
        existing = github.view_pr_for_branch(runner, branch)
        if existing:
            print(f"이미 열린 PR: {existing}")
            return existing

    issue_number = _issue_number(runner, wizard, branch)
    default_summary = conventions.strip_commit_subject_prefix(gitops.last_commit_subject(runner))
    while True:
        summary = wizard.text("pr_summary", "PR 변경 사항 요약:", default=default_summary).strip()
        try:
            title = conventions.pr_title(issue_number, summary)
            break
        except conventions.ConventionError as exc:
            print(exc)
    changes = _changes(wizard)
    breaking_commit = conventions.messages_include_breaking(gitops.commit_messages_since(runner, origin_base))
    build = wizard.confirm("checklist_build", "로컬 빌드/테스트가 통과했나요?", default=not skip_ruff and not dry_run)
    docs = wizard.confirm("checklist_docs", "관련 문서를 업데이트했나요?", default=False)
    breaking = wizard.confirm("checklist_breaking", "Breaking Change 여부를 확인했나요?", default=breaking_commit)
    breaking_notes = ""
    if breaking_commit:
        while not breaking_notes:
            breaking_notes = wizard.text("breaking_notes", "Breaking Change 내용을 설명하세요:").strip()
            if not breaking_notes:
                print("Breaking Change 내용은 필수입니다.")
    elif breaking:
        breaking_notes = wizard.text("breaking_notes", "Breaking Change 내용(선택):").strip()
    notes = wizard.text("pr_notes", "기타 참고 사항(선택):").strip()
    body = conventions.pr_body(
        issue_number=issue_number,
        changes=changes,
        checklist_build=build,
        checklist_docs=docs,
        checklist_breaking=breaking or breaking_commit,
        breaking_notes=breaking_notes,
        notes=notes,
    )
    print(f"PR 미리보기: {title}\n{body}")
    if not wizard.confirm("ship_confirm", "이 내용으로 PR을 열까요?", default=True):
        raise KtbError("PR 생성을 취소했습니다.")
    url = github.create_pr(runner, base=base, title=title, body=body, dry_run=dry_run)
    print(f"PR {'생성 예정' if dry_run else '생성 완료'}: {url or title}")
    return url
