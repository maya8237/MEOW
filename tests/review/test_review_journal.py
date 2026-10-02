"""Review commands retain their own result and failure checkpoints."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from meow.review_cli import run_review_command
from meow.run_state import RunStore


def test_review_command_records_completion(tmp_path):
    with (
        patch("meow.review_cli.load_config", return_value={"lint": []}),
        patch("meow.review_cli.describe_lint_plan"),
        patch("meow.review_cli._prompt_review", new_callable=AsyncMock),
    ):
        asyncio.run(run_review_command(tmp_path, "inspect", fix=False))
    record = RunStore(tmp_path).latest()
    assert record.source == "review"
    assert record.phase == "review_finished"


def test_review_command_retains_failure(tmp_path):
    with (
        patch("meow.review_cli.load_config", return_value={"lint": []}),
        patch("meow.review_cli.describe_lint_plan"),
        patch(
            "meow.review_cli._prompt_review",
            new=AsyncMock(side_effect=RuntimeError("review crashed")),
        ),
        pytest.raises(RuntimeError, match="review crashed"),
    ):
        asyncio.run(run_review_command(tmp_path, "inspect", fix=True))
    record = RunStore(tmp_path).latest()
    assert record.phase == "failed"
    assert record.last_failure == "review crashed"
