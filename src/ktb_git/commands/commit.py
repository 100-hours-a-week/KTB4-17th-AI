from __future__ import annotations

from ktb_git import conventions, gitops, lint
from ktb_git.errors import KtbError
from ktb_git.runner import CommandError, Runner
from ktb_git.wizard import Choice, Wizard


def _subject(wizard: Wizard) -> str:
    while True:
        subject = wizard.text("commit_subject", "커밋 제목(50자 이내, 마침표 없이):").strip()
        try:
            return conventions.validate_subject(subject)
        except conventions.ConventionError as exc:
            print(exc)


def run(
    runner: Runner,
    wizard: Wizard,
    *,
    dry_run: bool = False,
    skip_ruff: bool = False,
) -> str:
    branch = gitops.current_branch(runner)
    if branch == "main":
        raise KtbError("main 브랜치에서 직접 커밋할 수 없습니다. ktb start로 작업 브랜치를 만드세요.")
    initial_files = gitops.dirty_files(runner)
    if not initial_files:
        raise KtbError("커밋할 변경이 없습니다.")
    staged_before = gitops.name_only_paths(runner, staged=True)

    if skip_ruff:
        print("경고: --skip-ruff로 Ruff 검사를 건너뜁니다.")
    else:
        try:
            lint.ruff_fix(runner, dry_run=dry_run)
        except CommandError as exc:
            raise KtbError(f"Ruff 수정에 실패해 커밋을 중단합니다.\n{exc}") from exc

    files = gitops.dirty_files(runner) or initial_files
    ruff_touched_staged = sorted(set(staged_before) & set(gitops.name_only_paths(runner, staged=False)))
    if ruff_touched_staged and not dry_run:
        gitops.stage_paths(runner, ruff_touched_staged)
        print(f"Ruff가 바꾼 스테이징 파일을 다시 추가했습니다: {', '.join(ruff_touched_staged)}")

    stage_mode = wizard.select(
        "stage_mode",
        "스테이징 방식을 선택하세요:",
        [
            Choice("변경 파일 전부", "all"),
            Choice("파일 선택", "select"),
            Choice("이미 스테이징된 파일만", "staged"),
        ],
        default="all",
    )
    if stage_mode == "all":
        gitops.stage_paths(runner, files, dry_run=dry_run)
    elif stage_mode == "select":
        chosen = wizard.select_many(
            "stage_paths",
            "커밋할 파일을 선택하세요:",
            [Choice(path, path) for path in files],
        )
        if not chosen and not staged_before:
            raise KtbError("커밋할 파일을 하나 이상 선택하세요.")
        gitops.stage_paths(runner, chosen, dry_run=dry_run)
    elif not staged_before:
        raise KtbError("이미 스테이징된 파일이 없습니다.")

    commit_type = str(
        wizard.select(
            "commit_type",
            "커밋 타입을 선택하세요:",
            [Choice(kind, kind) for kind in conventions.COMMIT_TYPES],
            default="feat",
        )
    )
    breaking = wizard.confirm("breaking", "Breaking Change가 있나요?", default=False)
    subject = _subject(wizard)
    body = wizard.text("commit_body", "본문(선택):").strip()
    while breaking and not body:
        print("Breaking Change에는 본문이 필요합니다.")
        body = wizard.text("commit_body", "Breaking Change 본문:").strip()
    footer = wizard.text("commit_footer", "Footer(선택):").strip()
    linked_issue = gitops.get_branch_issue(runner, branch)
    resolves = None
    if linked_issue and wizard.confirm("resolve_issue", f"Resolves: #{linked_issue}를 넣을까요?", default=True):
        resolves = linked_issue
    message = conventions.assemble_commit_message(
        commit_type=commit_type,
        breaking=breaking,
        subject=subject,
        body=body,
        footer=footer,
        resolves=resolves,
    )
    print(f"커밋 미리보기:\n{message}")
    if not wizard.confirm("commit_confirm", "이 메시지로 커밋할까요?", default=True):
        raise KtbError("커밋을 취소했습니다.")
    gitops.commit(runner, message, dry_run=dry_run)
    print("커밋 완료. 다음: ktb ship으로 Push하고 PR을 여세요.")
    return message
