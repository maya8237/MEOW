"""The error a run dies of must be the one the user sees."""

import asyncio
from unittest.mock import patch

import pytest

from meow.agents.planner import require_plan_file
from meow.execution import sprint_runner
from meow.execution.run_state import RunStore


def test_corrupt_journal_does_not_mask_the_real_failure(tmp_path):
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text("", encoding="utf-8")
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="b"
    )

    async def boom(*_args, **_kwargs):  # ruff: ignore[unused-async]
        (store.directory / f"{record.id}.json").write_text("{", encoding="utf-8")
        raise RuntimeError("the real failure")

    with (
        patch.object(sprint_runner, "_drive_sprint", boom),
        pytest.raises(RuntimeError, match="the real failure"),
    ):
        asyncio.run(sprint_runner.run_sprint(tmp_path, "f", "x", run_id=record.id))


def test_empty_plan_file_is_not_a_plan(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("  \n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="empty"):
        require_plan_file(plan)
    plan.write_text("# Plan\n", encoding="utf-8")
    assert require_plan_file(plan) == plan
