from pathlib import Path

import pytest

from ktb_git import lint
from ktb_git.commands import ship
from ktb_git.errors import KtbError
from ktb_git.runner import Completed, FakeRunner
from ktb_git.wizard import FakeWizard


def _runner(root: Path, branch: str, *, pr_url: str | None = None) -> FakeRunner:
    runner = FakeRunner()
    base = "main" if branch.startswith("hotfix/") else "dev"
    runner.script(
        ["git", "rev-parse", "--show-toplevel"],
        Completed([], 0, f"{root}\n", ""),
    )
    runner.script(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        Completed([], 0, f"{branch}\n", ""),
    )
    runner.script(
        ["git", "rev-list", "--count", f"origin/{base}..HEAD"],
        Completed([], 0, "1\n", ""),
    )
    if pr_url:
        runner.script(
            ["gh", "pr", "view"],
            Completed([], 0, f'{{"url":"{pr_url}"}}', ""),
        )
    else:
        runner.script(
            ["gh", "pr", "view"],
            Completed([], 1, "", "no pull requests found"),
        )
    runner.script(
        ["git", "config", f"branch.{branch}.ktbIssue"],
        Completed([], 0, "42\n", ""),
    )
    runner.script(
        ["git", "log", "-1", "--pretty=%s"],
        Completed([], 0, "feat: add login\n", ""),
    )
    runner.script(
        ["git", "log", f"origin/{base}..HEAD", "--format=%B%x00"],
        Completed([], 0, "feat: add login\n\0", ""),
    )
    runner.script(
        ["gh", "pr", "create"],
        Completed([], 0, "https://github.com/org/repo/pull/9\n", ""),
    )
    return runner


def _wizard() -> FakeWizard:
    return FakeWizard(
        {
            "pr_summary": [None],
            "pr_changes": ["add login"],
            "checklist_build": [None],
            "checklist_docs": [False],
            "checklist_breaking": [False],
            "pr_notes": [""],
            "ship_confirm": [True],
        }
    )


def test_feature_ship_targets_dev(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lint, "ensure_uv", lambda: None)
    runner = _runner(tmp_path, "feat/garnet-login")

    url = ship.run(runner, _wizard())

    assert url == "https://github.com/org/repo/pull/9"
    pr_call = next(call for call in runner.calls if call[:3] == ["gh", "pr", "create"])
    assert pr_call[pr_call.index("--base") + 1] == "dev"
    assert ["git", "push", "-u", "origin", "HEAD"] in runner.calls


def test_hotfix_ship_targets_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lint, "ensure_uv", lambda: None)
    runner = _runner(tmp_path, "hotfix/garnet-urgent")

    ship.run(runner, _wizard())

    pr_call = next(call for call in runner.calls if call[:3] == ["gh", "pr", "create"])
    assert pr_call[pr_call.index("--base") + 1] == "main"


def test_existing_pr_skips_create(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lint, "ensure_uv", lambda: None)
    existing = "https://github.com/org/repo/pull/3"
    runner = _runner(tmp_path, "feat/garnet-login", pr_url=existing)

    assert ship.run(runner, FakeWizard()) == existing

    assert not any(call[:3] == ["gh", "pr", "create"] for call in runner.calls)


def test_feature_to_feature_base_is_refused_before_push(tmp_path: Path) -> None:
    (tmp_path / ".ktb.toml").write_text('default_base = "feat/other"\n', encoding="utf-8")
    runner = _runner(tmp_path, "feat/garnet-login")

    with pytest.raises(KtbError, match="대상"):
        ship.run(runner, FakeWizard())

    assert not any(call[:2] == ["git", "push"] for call in runner.calls)
    assert not any(call[:3] == ["gh", "pr", "create"] for call in runner.calls)
