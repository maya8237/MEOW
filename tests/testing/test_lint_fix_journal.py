"""The lint-fix run mode keeps an inspectable checkpoint."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from meow.lint_fix import run_lint_fix
from meow.run_state import RunStore


def test_lint_fix_failure_keeps_run_record(tmp_path):
    with (
        patch("meow.lint_fix.load_config", return_value={"lint": []}),
        patch("meow.lint_fix.describe_lint_plan"),
        patch(
            "meow.lint_fix._fix_until_clean",
            new=AsyncMock(side_effect=RuntimeError("fix failed")),
        ),
        pytest.raises(RuntimeError, match="fix failed"),
    ):
        asyncio.run(run_lint_fix(tmp_path, report_only=False))
    record = RunStore(tmp_path).latest()
    assert record.source == "lint-fix"
    assert record.phase == "failed"
    assert record.last_failure == "fix failed"
