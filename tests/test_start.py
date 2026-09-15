from pathlib import Path

from ktb_git import config
from ktb_git.commands import start
from ktb_git.runner import Completed, FakeRunner
from ktb_git.wizard import FakeWizard


def _runner(root: Path, branch: str = "dev") -> FakeRunner:
    runner = FakeRunner()
    runner.script(
        ["git", "rev-parse", "--show-toplevel"],
        Completed([], 0, f"{root}\n", ""),
    )
    runner.script(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        Completed([], 0, f"{branch}\n", ""),
    )
    return runner


def _new_issue_answers() -> dict[str, list[object]]:
    return {
        "issue_mode": ["new"],
        "issue_type": ["feat"],
        "assignee": [None],
        "issue_summary": ["login"],
        "branch_kind": [None],
        "feature_slug": [None],
        "start_confirm": [True],
    }


def test_start_creates_issue_and_feature_branch(tmp_path: Path) -> None:
    config_path = tmp_path / "user.toml"
    config.save_user_config(config.UserConfig("가넷", "garnet"), config_path)
    runner = _runner(tmp_path)
    runner.script(
        ["gh", "issue", "create"],
        Completed([], 0, "https://github.com/org/repo/issues/42\n", ""),
    )
    wizard = FakeWizard(_new_issue_answers())

    branch = start.run(runner, wizard, config_path=config_path)

    assert branch == "feat/garnet-login"
    assert [
        "gh",
        "issue",
        "create",
        "--title",
        "[feat][가넷] login",
        "--body",
        "이슈 분류: feat\n담당: 가넷\n",
    ] in runner.calls
    assert ["git", "fetch", "origin"] in runner.calls
    assert ["git", "checkout", "origin/dev"] in runner.calls
    assert ["git", "checkout", "-b", "feat/garnet-login", "origin/dev"] in runner.calls
    assert ["git", "config", "branch.feat/garnet-login.ktbIssue", "42"] in runner.calls
    assert not any(call[:2] == ["git", "push"] for call in runner.calls)


def test_start_existing_hotfix_uses_main(tmp_path: Path) -> None:
    config_path = tmp_path / "user.toml"
    config.save_user_config(config.UserConfig("가넷", "garnet"), config_path)
    runner = _runner(tmp_path)
    runner.script(
        ["gh", "issue", "list"],
        Completed([], 0, '[{"number":7,"title":"[hotfix][가넷] urgent"}]', ""),
    )
    wizard = FakeWizard(
        {
            "issue_mode": ["existing"],
            "existing_issue": [7],
            "branch_kind": [None],
            "feature_slug": ["urgent"],
            "start_confirm": [True],
        }
    )

    start.run(runner, wizard, config_path=config_path)

    assert ["git", "checkout", "origin/main"] in runner.calls
    assert ["git", "checkout", "-b", "hotfix/garnet-urgent", "origin/main"] in runner.calls
    assert not any(call[:3] == ["gh", "issue", "create"] for call in runner.calls)


def test_missing_user_config_prompts_and_saves(tmp_path: Path) -> None:
    config_path = tmp_path / "user.toml"
    runner = _runner(tmp_path)
    runner.script(
        ["gh", "issue", "create"],
        Completed([], 0, "https://github.com/org/repo/issues/42\n", ""),
    )
    answers = _new_issue_answers()
    answers.update({"nickname": ["가넷"], "nickname_slug": ["garnet"]})

    start.run(runner, FakeWizard(answers), config_path=config_path)

    assert config.load_user_config(config_path) == config.UserConfig("가넷", "garnet")
