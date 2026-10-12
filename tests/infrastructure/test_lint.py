"""Lint commands that cannot start are failures, not crashes."""

import asyncio

from meow.infrastructure.lint import check_lint_evidence
from meow.project.config_models import LintCommand


def test_missing_lint_program_is_a_blocking_failure(tmp_path):
    command = LintCommand("no-such-linter-xyz", per_file=False)
    evidence = asyncio.run(check_lint_evidence(tmp_path, [command], 10))
    assert evidence.blocking_failed
    assert "Could not run lint command" in evidence.blocking[0]


def test_lint_command_with_a_missing_cwd_is_a_blocking_failure(tmp_path):
    from pathlib import Path

    command = LintCommand("ruff check", per_file=False, cwd=Path("frontend"))
    evidence = asyncio.run(check_lint_evidence(tmp_path, [command], 10))
    assert evidence.blocking_failed
    assert "frontend" in evidence.blocking[0]


def test_advisory_lint_command_that_cannot_start_stays_advisory(tmp_path):
    command = LintCommand('"unterminated', per_file=False, gate=False)
    evidence = asyncio.run(check_lint_evidence(tmp_path, [command], 10))
    assert not evidence.blocking_failed
    assert evidence.informational


def test_per_file_hook_reports_a_missing_cwd_instead_of_raising(tmp_path):
    from pathlib import Path

    from meow.infrastructure.lint import run_lint_on_file

    command = LintCommand("ruff check", cwd=Path("frontend"))
    problems = asyncio.run(run_lint_on_file(tmp_path, [command], "frontend/a.py", 10))
    assert problems and "frontend" in problems[0]


def test_fix_pass_skips_a_command_that_cannot_start(tmp_path):
    from meow.infrastructure.lint import apply_lint_fixes

    command = LintCommand("ruff check", fix_flag="--fix", cwd=tmp_path / "nope")
    asyncio.run(apply_lint_fixes(tmp_path, [command], 10))


def test_timed_out_lint_command_leaves_no_orphans(tmp_path):
    import asyncio
    import sys
    import time

    from meow.infrastructure.lint import _run_subprocess

    marker = tmp_path / "survivor"
    child = f"import time;time.sleep(1.5);open(r'{marker}','w').close()"
    code = (
        "import subprocess, sys, time;"
        f"subprocess.Popen([sys.executable, '-c', {child!r}]);"
        "time.sleep(60)"
    )
    result = asyncio.run(_run_subprocess(tmp_path, [sys.executable, "-c", code], 0.5))
    assert result is None
    time.sleep(2.5)
    assert not marker.exists()
