"""Mutating round boundaries are durable before an agent starts."""

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meow.execution.orchestrator import ReviewTestResult, _run_rounds
from meow.execution.run_state import RunStore
from meow.execution.sprint import Sprint
from meow.execution.sprint_runner import run_sprint
from meow.infrastructure.checks import code_revision
from meow.infrastructure.worktree import _ensure_clean_tree


def test_interrupted_generator_is_recorded_before_edit(tmp_path):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    sprint = Sprint(
        tmp_path, {"max_rounds": 1, "_run_journal": (store, record.id)}, None, None
    )
    generator = MagicMock()
    generator.__aenter__ = AsyncMock(return_value=generator)
    generator.__aexit__ = AsyncMock(return_value=False)
    generator.implement = AsyncMock(side_effect=KeyboardInterrupt)
    with (
        patch("meow.execution.orchestrator.Generator", return_value=generator),
        pytest.raises(KeyboardInterrupt),
    ):
        asyncio.run(_run_rounds(sprint, Path("plan.md")))
    assert store.load(record.id).phase == "interrupted_mutation"
    assert [x["phase"] for x in store.load(record.id).transitions][-2:] == [
        "generator_started",
        "interrupted_mutation",
    ]


def test_run_records_planning_and_completion(tmp_path):
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text("", encoding="utf-8")
    plan = tmp_path / "plan.md"
    plan.write_text("plan", encoding="utf-8")
    config = {
        "max_rounds": 1,
        "docs_dir": ".",
        "lint": [],
        "lint_timeout": 60,
        "tester": {"tests": [], "dev_server": []},
        "build": [],
    }
    sprint = Sprint(tmp_path, config, None, None)

    async def passed(*args, **kwargs):  # ruff: ignore[unused-async]
        store, run_id = sprint.config["_run_journal"]
        store.transition(
            run_id,
            "reviewer_finished",
            results={
                "reviewer": "PASS",
                "reviewer_revision": code_revision(tmp_path),
            },
        )
        return True

    with (
        patch(
            "meow.execution.sprint_runner._prepare_sprint",
            return_value=(sprint, "x", tmp_path),
        ),
        patch("meow.execution.sprint_runner.PlannerAgent") as planner,
        patch("meow.execution.sprint_runner._run_rounds", side_effect=passed),
        patch("meow.execution.sprint_runner.deliver_verified_run") as deliver,
    ):
        planner.return_value.run = AsyncMock(return_value=plan)
        asyncio.run(run_sprint(tmp_path, "x", "request", use_worktree=False))
    record = RunStore(tmp_path).latest()
    assert record.phase == "complete"
    assert record.plan_file == str(plan)
    deliver.assert_called_once()
    assert deliver.call_args.args[0].directory == RunStore(tmp_path).directory
    assert deliver.call_args.args[1] == record.id
    assert deliver.call_args.kwargs == {"unattended": False}
    assert "planning" in [item["phase"] for item in record.transitions]


def test_multitask_plan_uses_isolated_executor_then_final_review(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("# plan\n", encoding="utf-8")
    (tmp_path / "plan.md.tasks.json").write_text(
        '{"tasks":['
        '{"id":"api","depends_on":[],"owned_paths":["src/api"],"verification":[]},'
        '{"id":"ui","depends_on":[],"owned_paths":["src/ui"],"verification":[]}'
        "]}",
        encoding="utf-8",
    )
    sprint = Sprint(
        tmp_path,
        {
            "max_rounds": 1,
            "docs_dir": ".",
            "lint": [],
            "lint_timeout": 60,
            "tester": {"tests": [], "dev_server": []},
            "build": [],
        },
        None,
        None,
    )
    with (
        patch(
            "meow.execution.sprint_runner._prepare_sprint",
            return_value=(sprint, "x", tmp_path),
        ),
        patch(
            "meow.execution.sprint_runner.run_parallel_plan",
            new=AsyncMock(return_value=True),
        ) as parallel,
        patch(
            "meow.execution.sprint_runner._run_rounds",
            new=AsyncMock(side_effect=AssertionError("generator first")),
        ),
        patch(
            "meow.execution.sprint_runner._run_review_rounds",
            new=AsyncMock(return_value=True),
        ) as review,
    ):
        asyncio.run(
            run_sprint(tmp_path, "x", "request", plan_file=plan, use_worktree=False)
        )
    parallel.assert_awaited_once()
    review.assert_awaited_once()


def test_prepare_failure_keeps_a_run_checkpoint(tmp_path):
    with (
        patch(
            "meow.execution.sprint_runner._prepare_sprint",
            side_effect=RuntimeError("setup failed"),
        ),
        pytest.raises(RuntimeError, match="setup failed"),
    ):
        asyncio.run(run_sprint(tmp_path, "x", "request", use_worktree=False))
    record = RunStore(tmp_path).latest()
    assert record.phase == "failed"
    assert record.last_failure == "setup failed"


def test_run_journal_does_not_make_target_repo_dirty(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    RunStore(tmp_path).create(
        source="prompt", request="work", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    _ensure_clean_tree(tmp_path)


def test_tester_failure_keeps_independent_reviewer_verdict(tmp_path):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    sprint = Sprint(
        tmp_path, {"max_rounds": 1, "_run_journal": (store, record.id)}, None, None
    )
    generator = MagicMock()
    generator.__aenter__ = AsyncMock(return_value=generator)
    generator.__aexit__ = AsyncMock(return_value=False)
    generator.implement = AsyncMock()
    with (
        patch("meow.execution.orchestrator.Generator", return_value=generator),
        patch(
            "meow.execution.orchestrator.review_then_test",
            new=AsyncMock(
                return_value=ReviewTestResult("FAIL", "tester fail", "PASS", "FAIL")
            ),
        ),
    ):
        assert not asyncio.run(_run_rounds(sprint, tmp_path / "plan.md", test=True))
    assert store.load(record.id).results["reviewer"] == "PASS"


def test_final_check_exception_records_failure(tmp_path):
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text("", encoding="utf-8")
    plan = tmp_path / "plan.md"
    plan.write_text("plan", encoding="utf-8")
    config = {
        "max_rounds": 1,
        "docs_dir": ".",
        "lint": [],
        "lint_timeout": 60,
        "tester": {"tests": []},
        "build": [],
    }
    sprint = Sprint(tmp_path, config, None, None)

    async def passed(*args, **kwargs):  # ruff: ignore[unused-async]
        store, run_id = sprint.config["_run_journal"]
        store.transition(
            run_id,
            "reviewer_finished",
            results={
                "reviewer": "PASS",
                "reviewer_revision": code_revision(tmp_path),
            },
        )
        return True

    with (
        patch(
            "meow.execution.sprint_runner._prepare_sprint",
            return_value=(sprint, "x", tmp_path),
        ),
        patch("meow.execution.sprint_runner._run_rounds", side_effect=passed),
        patch(
            "meow.execution.sprint_runner.run_final_checks",
            new=AsyncMock(side_effect=RuntimeError("check crashed")),
        ),
        pytest.raises(RuntimeError, match="check crashed"),
    ):
        asyncio.run(
            run_sprint(tmp_path, "x", "request", use_worktree=False, plan_file=plan)
        )
    record = RunStore(tmp_path).latest()
    assert record.phase == "failed"
    assert record.last_failure == "check crashed"
