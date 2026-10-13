"""Failures that reach the CLI's outermost handler end as one message, exit 1."""

import subprocess
from unittest.mock import patch

import pytest
from claude_agent_sdk import ProcessError

from meow.cli import cli


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
def test_unexpected_failures_exit_one_without_a_traceback(error, expected, capsys):
    with (
        patch("meow.execution.run_policy._ensure_clean_tree"),
        patch("meow.cli.cli._boot_repo"),
        patch("meow.cli.cli._dispatch", side_effect=error),
        patch("sys.argv", ["meow", "run", "x", "--name", "n"]),
        pytest.raises(SystemExit) as raised,
    ):
        cli.cli_main()
    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert expected in err
    assert "Traceback" not in err


def test_resume_failure_exits_one_without_a_traceback(capsys):
    async def failing(*_args, **_kwargs):  # ruff: ignore[unused-async]
        raise RuntimeError("Parallel task execution stopped: a: failed")

    with (
        patch("meow.cli.cli.resume", failing),
        patch("sys.argv", ["meow", "resume", "a" * 32, "--continue"]),
        pytest.raises(SystemExit) as raised,
    ):
        cli.cli_main()
    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert "Parallel task execution stopped" in err
    assert "Traceback" not in err
