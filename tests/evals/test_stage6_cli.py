import argparse
from unittest.mock import patch

from evals._harness import stage6_cli as CLI


def test_cli_run_regression_blocked_when_preconditions_fail():
    # preflight 또는 manifest verify 가 실패하면 subprocess.run 호출 0회 확인
    args = argparse.Namespace(only="f01", limit=1, no_judge=True, version="", cases="")

    with patch("evals._harness.stage6_cli._verify_preconditions_before_run", return_value=False):
        with patch("subprocess.run") as mock_sub:
            ret = CLI.cmd_run_regression(args)
            assert ret == 1
            # 하위 프로세스 호출이 전혀 없어야 함 (호출 0회)
            assert mock_sub.call_count == 0


def test_cli_run_holdout_blocked_without_allow_flag():
    args = argparse.Namespace(allow_holdout=False, only="f01", limit=1, no_judge=True, version="")
    with patch("subprocess.run") as mock_sub:
        ret = CLI.cmd_run_holdout(args)
        assert ret == 1
        assert mock_sub.call_count == 0


def test_cli_run_holdout_blocked_when_preconditions_fail():
    args = argparse.Namespace(allow_holdout=True, only="f01", limit=1, no_judge=True, version="")
    with patch("evals._harness.stage6_cli._verify_preconditions_before_run", return_value=False):
        with patch("subprocess.run") as mock_sub:
            ret = CLI.cmd_run_holdout(args)
            assert ret == 1
            assert mock_sub.call_count == 0
