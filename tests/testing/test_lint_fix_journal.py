"""The lint-fix run mode keeps an inspectable checkpoint."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from meow.execution.run_state import RunStore
from meow.infrastructure.lint_fix import run_lint_fix


def test_lint_fix_failure_keeps_run_record(tmp_path):
    with (
        patch("meow.infrastructure.lint_fix.load_config", return_value={"lint": []}),
        patch("meow.infrastructure.lint_fix.describe_lint_plan"),
        patch(
            "meow.infrastructure.lint_fix._fix_until_clean",
            new=AsyncMock(side_effect=RuntimeError("fix failed")),
        ),
        pytest.raises(RuntimeError, match="fix failed"),
    ):
        asyncio.run(run_lint_fix(tmp_path, report_only=False))
    record = RunStore(tmp_path).latest()
    assert record.source == "lint-fix"
    assert record.phase == "failed"
    assert record.last_failure == "fix failed"


def test_lint_fix_onboards_an_unconfigured_project(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    with (
        patch("meow.infrastructure.lint_fix.describe_lint_plan"),
        patch(
            "meow.infrastructure.lint_fix._report_only",
            new=AsyncMock(return_value=None),
        ),
    ):
        asyncio.run(run_lint_fix(tmp_path, report_only=True))

    assert (tmp_path / ".meow" / "config.toml").is_file()
