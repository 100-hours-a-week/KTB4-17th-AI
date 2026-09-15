from __future__ import annotations

from ktb_git import status
from ktb_git.commands import commit, init, ship, start
from ktb_git.conventions import ConventionError
from ktb_git.errors import KtbError
from ktb_git.runner import CommandError, Runner
from ktb_git.wizard import Choice, Wizard


def run(runner: Runner, wizard: Wizard) -> None:
    while True:
        state = status.run(runner)
        commit_reason = None
        if not state.can_commit:
            commit_reason = "main에서는 커밋할 수 없습니다" if state.branch == "main" else "변경 파일이 없습니다"
        ship_reason = None
        if not state.can_ship:
            ship_reason = "작업 브랜치에 기준 브랜치보다 앞선 커밋이 필요합니다"
        action = wizard.select(
            "hub_action",
            "다음 작업을 선택하세요:",
            [
                Choice("이슈 만들고 브랜치 시작", "start"),
                Choice("커밋", "commit", commit_reason),
                Choice("Push하고 PR 열기", "ship", ship_reason),
                Choice("설정", "init"),
                Choice("종료", "exit"),
            ],
        )
        if action == "exit":
            return
        try:
            if action == "start":
                start.run(runner, wizard)
            elif action == "commit":
                commit.run(runner, wizard)
            elif action == "ship":
                ship.run(runner, wizard)
            elif action == "init":
                init.run(runner, wizard)
        except (KtbError, ConventionError, CommandError) as exc:
            print(exc)
