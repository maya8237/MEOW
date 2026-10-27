"""Failures that reach the CLI's outermost handler end as one message.

A run command with no blocking cause exits 75 with a `MEOW_RETRY` line."""

import subprocess
from unittest.mock import patch

import pytest
from claude_agent_sdk import ProcessError

from meow.cli import cli
from meow.cli.run_contract import EXIT_TEMPFAIL


class _Quit(RuntimeError):
    pass


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("step timed out"), "step timed out"),
        (PermissionError("denied"), "denied"),
        (ProcessError("CLI startup failed", exit_code=-11), "CLI startup failed"),
        (
            subprocess.CalledProcessError(
                128, ["git", "worktree", "add"], stderr="boom"
            ),
            "git worktree add",
        ),
        (ExceptionGroup("branches", [OSError("disk full")]), "disk full"),
    ],
)
def test_unexpected_failures_exit_75_without_a_traceback(error, expected, capsys):
    with (
        patch("meow.execution.run_policy._ensure_clean_tree"),
        patch("meow.cli.cli._boot_repo"),
        patch("meow.cli.cli._dispatch", side_effect=error),
        patch("sys.argv", ["meow", "run", "x", "--name", "n"]),
        pytest.raises(SystemExit) as raised,
    ):
        cli.cli_main()
    assert raised.value.code == EXIT_TEMPFAIL
    err = capsys.readouterr().err
    assert "MEOW_RETRY " in err
    assert expected in err
    assert "Traceback" not in err


def test_resume_failure_exits_75_without_a_traceback(capsys):
    async def failing(*_args, **_kwargs):  # ruff: ignore[unused-async]
        raise RuntimeError("Parallel task execution stopped: a: failed")

    with (
        patch("meow.cli.cli.resume", failing),
        patch("sys.argv", ["meow", "resume", "a" * 32, "--continue"]),
        pytest.raises(SystemExit) as raised,
    ):
        cli.cli_main()
    assert raised.value.code == EXIT_TEMPFAIL
    err = capsys.readouterr().err
    assert "MEOW_RETRY " in err
    assert "Parallel task execution stopped" in err
    assert "Traceback" not in err
