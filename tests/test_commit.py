from pathlib import Path
from typing import Any

import pytest

from ktb_git import lint
from ktb_git.commands import commit
from ktb_git.errors import KtbError
from ktb_git.runner import Completed, FakeRunner
from ktb_git.wizard import FakeWizard


class CapturingRunner(FakeRunner):
    def __init__(self) -> None:
        super().__init__()
        self.commit_message: str | None = None

    def run(self, args: list[str], **kwargs: Any) -> Completed:
        if args[:3] == ["git", "commit", "-F"]:
            self.commit_message = Path(args[3]).read_text(encoding="utf-8")
        return super().run(args, **kwargs)


def _runner(*, branch: str = "feat/garnet-login", staged: str = "file.py\0") -> CapturingRunner:
    runner = CapturingRunner()
    runner.script(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        Completed([], 0, f"{branch}\n", ""),
    )
    status = Completed([], 0, " M file.py\0", "")
    runner.script(["git", "status", "--porcelain=v1", "-z"], status)
    runner.script(["git", "status", "--porcelain=v1", "-z"], status)
    runner.script(["git", "diff", "--cached", "--name-only", "-z"], Completed([], 0, staged, ""))
    runner.script(["git", "diff", "--name-only", "-z"], Completed([], 0, "file.py\0", ""))
    runner.script(
        ["git", "config", f"branch.{branch}.ktbIssue"],
        Completed([], 0, "42\n", ""),
    )
    return runner


def test_breaking_commit_uses_tempfile_and_resolves_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lint, "ensure_uv", lambda: None)
    runner = _runner()
    wizard = FakeWizard(
        {
            "stage_mode": ["all"],
            "commit_type": ["feat"],
            "breaking": [True],
            "commit_subject": ["add login"],
            "commit_body": ["API changed"],
            "commit_footer": [""],
            "resolve_issue": [True],
            "commit_confirm": [True],
        }
    )

    message = commit.run(runner, wizard)

    assert message == runner.commit_message
    assert message.startswith("feat!: add login\n\nAPI changed")
    assert "BREAKING CHANGE: add login" in message
    assert "Resolves: #42" in message
    assert ["uv", "run", "ruff", "check", "--fix", "."] in runner.calls
    assert ["uv", "run", "ruff", "format", "."] in runner.calls
    assert any(call[:3] == ["git", "commit", "-F"] for call in runner.calls)
    assert not any(call[:2] == ["git", "push"] for call in runner.calls)


def test_ruff_touched_staged_file_is_restaged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lint, "ensure_uv", lambda: None)
    runner = _runner()
    wizard = FakeWizard(
        {
            "stage_mode": ["staged"],
            "commit_type": ["fix"],
            "breaking": [False],
            "commit_subject": ["repair login"],
            "commit_body": [""],
            "commit_footer": [""],
            "resolve_issue": [False],
            "commit_confirm": [True],
        }
    )

    commit.run(runner, wizard)

    assert ["git", "add", "--", "file.py"] in runner.calls
    assert runner.calls.index(["git", "add", "--", "file.py"]) < next(
        index for index, call in enumerate(runner.calls) if call[:3] == ["git", "commit", "-F"]
    )


def test_commit_refuses_main() -> None:
    runner = CapturingRunner()
    runner.script(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        Completed([], 0, "main\n", ""),
    )

    with pytest.raises(KtbError, match="main"):
        commit.run(runner, FakeWizard())

    assert not any(call[:3] == ["git", "commit", "-F"] for call in runner.calls)
