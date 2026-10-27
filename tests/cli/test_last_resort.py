"""Failures that reach the CLI's outermost handler end as one message.

With no run record, only a recognised transient failure (timeout, SDK
process, OS I/O) exits 75 with `MEOW_RETRY`; anything else exits 1 with
`MEOW_BLOCKED`, so a managing agent never retries it unbounded."""

import subprocess
from unittest.mock import patch

import pytest
from claude_agent_sdk import ProcessError

from meow.cli import cli
from meow.cli.run_contract import EXIT_BLOCKED, EXIT_TEMPFAIL


class _Quit(RuntimeError):
    pass


@pytest.mark.parametrize(
    ("error", "expected", "code"),
    [
        (TimeoutError("step timed out"), "step timed out", EXIT_TEMPFAIL),
        (PermissionError("denied"), "denied", EXIT_BLOCKED),
        (
            ProcessError("CLI startup failed", exit_code=-11),
            "CLI startup failed",
            EXIT_TEMPFAIL,
        ),
        (
            subprocess.CalledProcessError(
                128, ["git", "worktree", "add"], stderr="boom"
            ),
            "git worktree add",
            EXIT_BLOCKED,
        ),
        (
            ExceptionGroup("branches", [OSError("disk full")]),
            "disk full",
            EXIT_TEMPFAIL,
        ),
    ],
)
def test_unexpected_failures_exit_cleanly(error, expected, code, capsys):
    with (
        patch("meow.execution.run_policy._ensure_clean_tree"),
        patch("meow.cli.cli._boot_repo"),
        patch("meow.cli.cli._dispatch", side_effect=error),
        patch("sys.argv", ["meow", "run", "x", "--name", "n"]),
        pytest.raises(SystemExit) as raised,
    ):
        cli.cli_main()
    assert raised.value.code == code
    err = capsys.readouterr().err
    assert ("MEOW_RETRY " if code == EXIT_TEMPFAIL else "MEOW_BLOCKED ") in err
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
    assert raised.value.code == EXIT_BLOCKED
    err = capsys.readouterr().err
    assert "MEOW_BLOCKED " in err
    assert "Parallel task execution stopped" in err
    assert "Traceback" not in err
