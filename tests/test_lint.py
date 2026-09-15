import pytest

from ktb_git import lint
from ktb_git.commands import commit, ship
from ktb_git.errors import KtbError
from ktb_git.runner import CommandError, Completed, FakeRunner
from ktb_git.wizard import FakeWizard


def test_ruff_fix_exact_command_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lint, "ensure_uv", lambda: None)
    runner = FakeRunner()
    runner.script(["uv", "run", "ruff", "check"], Completed([], 0, "fixed", ""))

    lint.ruff_fix(runner)

    assert runner.calls == [
        ["uv", "run", "ruff", "check", "--fix", "."],
        ["uv", "run", "ruff", "format", "."],
    ]


def test_ruff_check_exact_command_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lint, "ensure_uv", lambda: None)
    runner = FakeRunner()
    runner.script(
        ["uv", "run", "ruff", "format", "--check", "."],
        Completed([], 0, "clean", ""),
    )

    lint.ruff_check(runner)

    assert runner.calls == [
        ["uv", "run", "ruff", "format", "--check", "."],
        ["uv", "run", "ruff", "check", "."],
    ]


def test_ruff_failure_stops_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lint, "ensure_uv", lambda: None)
    runner = FakeRunner()
    first = ["uv", "run", "ruff", "format", "--check", "."]
    runner.script(first, Completed(first, 1, "", "format failed"))

    with pytest.raises(CommandError, match="format failed"):
        lint.ruff_check(runner)

    assert runner.calls == [first]


def test_missing_uv_has_install_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lint.shutil, "which", lambda command: None)

    with pytest.raises(KtbError, match=lint.UV_INSTALL_URL):
        lint.ensure_uv()


def _commit_runner() -> FakeRunner:
    runner = FakeRunner()
    runner.script(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        Completed([], 0, "feat/garnet-login\n", ""),
    )
    for _ in range(2):
        runner.script(
            ["git", "status", "--porcelain=v1", "-z"],
            Completed([], 0, " M file.py\0", ""),
        )
    runner.script(
        ["git", "config", "branch.feat/garnet-login.ktbIssue"],
        Completed([], 1, "", ""),
    )
    return runner


def _commit_wizard() -> FakeWizard:
    return FakeWizard(
        {
            "stage_mode": ["all"],
            "commit_type": ["feat"],
            "breaking": [False],
            "commit_subject": ["add login"],
            "commit_body": [""],
            "commit_footer": [""],
            "commit_confirm": [True],
        }
    )


def _ship_runner(root) -> FakeRunner:
    runner = FakeRunner()
    runner.script(
        ["git", "rev-parse", "--show-toplevel"],
        Completed([], 0, f"{root}\n", ""),
    )
    runner.script(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        Completed([], 0, "feat/garnet-login\n", ""),
    )
    runner.script(
        ["git", "rev-list", "--count", "origin/dev..HEAD"],
        Completed([], 0, "1\n", ""),
    )
    runner.script(
        ["gh", "pr", "view"],
        Completed([], 0, '{"url":"https://github.com/org/repo/pull/3"}', ""),
    )
    return runner


def test_commit_invokes_exact_fix_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lint, "ensure_uv", lambda: None)
    runner = _commit_runner()

    commit.run(runner, _commit_wizard())

    ruff_calls = [call for call in runner.calls if call[:3] == ["uv", "run", "ruff"]]
    assert ruff_calls == [
        ["uv", "run", "ruff", "check", "--fix", "."],
        ["uv", "run", "ruff", "format", "."],
    ]


def test_ship_invokes_exact_check_sequence(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lint, "ensure_uv", lambda: None)
    runner = _ship_runner(tmp_path)

    ship.run(runner, FakeWizard())

    ruff_calls = [call for call in runner.calls if call[:3] == ["uv", "run", "ruff"]]
    assert ruff_calls == [
        ["uv", "run", "ruff", "format", "--check", "."],
        ["uv", "run", "ruff", "check", "."],
    ]


def test_skip_ruff_invokes_neither_command(tmp_path) -> None:
    commit_runner = _commit_runner()
    commit.run(commit_runner, _commit_wizard(), skip_ruff=True)
    ship_runner = _ship_runner(tmp_path)
    ship.run(ship_runner, FakeWizard(), skip_ruff=True)

    assert not any(call[:3] == ["uv", "run", "ruff"] for call in commit_runner.calls)
    assert not any(call[:3] == ["uv", "run", "ruff"] for call in ship_runner.calls)


def test_ruff_check_failure_prevents_push_and_pr(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lint, "ensure_uv", lambda: None)
    runner = _ship_runner(tmp_path)
    first = ["uv", "run", "ruff", "format", "--check", "."]
    runner.script(first, Completed(first, 1, "", "format failed"))

    with pytest.raises(KtbError, match="Push와 PR 생성을 중단"):
        ship.run(runner, FakeWizard())

    assert first in runner.calls
    assert not any(call[:2] == ["git", "push"] for call in runner.calls)
    assert not any(call[:3] == ["gh", "pr", "create"] for call in runner.calls)
