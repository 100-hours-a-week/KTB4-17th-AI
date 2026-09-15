from pathlib import Path

from ktb_git import status
from ktb_git.runner import Completed, FakeRunner


def _runner(root: Path, *, dirty: bool, ahead: int) -> FakeRunner:
    runner = FakeRunner()
    runner.script(
        ["git", "rev-parse", "--show-toplevel"],
        Completed([], 0, f"{root}\n", ""),
    )
    for _ in range(2):
        runner.script(
            ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
            Completed([], 0, "feat/garnet-login\n", ""),
        )
    runner.script(
        ["git", "status", "--porcelain=v1", "-z"],
        Completed([], 0, " M file.py\0" if dirty else "", ""),
    )
    runner.script(
        ["git", "config", "branch.feat/garnet-login.ktbIssue"],
        Completed([], 0, "42\n", ""),
    )
    runner.script(
        ["git", "rev-list", "--count", "origin/dev..HEAD"],
        Completed([], 0, f"{ahead}\n", ""),
    )
    runner.script(
        ["gh", "pr", "view"],
        Completed([], 1, "", "no pull requests found"),
    )
    return runner


def test_dirty_state_points_to_commit(tmp_path: Path, capsys) -> None:
    runner = _runner(tmp_path, dirty=True, ahead=1)

    state = status.run(runner)

    assert state.can_commit
    assert "ktb commit" in state.next_action
    assert "변경 파일: 1개" in capsys.readouterr().out


def test_unpushed_commit_without_pr_points_to_ship(tmp_path: Path) -> None:
    runner = _runner(tmp_path, dirty=False, ahead=2)

    state = status.snapshot(runner)

    assert state.can_ship
    assert state.pr_url is None
    assert "ktb ship" in state.next_action
