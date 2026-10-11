"""Lint commands that cannot start are failures, not crashes."""

import asyncio

from meow.infrastructure.lint import check_lint_evidence
from meow.project.config_models import LintCommand


def test_missing_lint_program_is_a_blocking_failure(tmp_path):
    command = LintCommand("no-such-linter-xyz", per_file=False)
    evidence = asyncio.run(check_lint_evidence(tmp_path, [command], 10))
    assert evidence.blocking_failed
    assert "Could not run lint command" in evidence.blocking[0]
