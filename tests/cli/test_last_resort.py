"""Failures that reach the CLI's outermost handler end as one message, exit 1."""

import subprocess
from unittest.mock import patch

import pytest

from meow.cli import cli


class _Quit(RuntimeError):
    pass


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("step timed out"), "step timed out"),
        (PermissionError("denied"), "denied"),
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
