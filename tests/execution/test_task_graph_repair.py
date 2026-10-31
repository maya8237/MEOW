"""Invalid planner graphs get one repair attempt before sequential fallback."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from meow.execution.workflow.builtin_steps import tasks
from meow.infrastructure.cancellation import RunCancelled
from tests.execution.test_workflow_engine import state_for


@pytest.mark.parametrize(
    "outcome", ["repaired", "invalid", "missing", "error", "cancel"]
)
def test_invalid_task_graph_repair(tmp_path, outcome):  # ruff: ignore[complex-structure, too-many-statements] -- exercise repair outcomes at the SDK boundary
    state = state_for(tmp_path, plan=True)
    state.sprint.config["models"]["planner"] = "pinned-planner"
    artifact = state.plan_file.with_suffix(".md.tasks.json")
    entries = [
        {
            "id": name,
            "depends_on": [],
            "owned_paths": ["src/weather"],
            "verification": [],
        }
        for name in ("service", "tests")
    ]
    artifact.write_text(json.dumps({"tasks": entries}), encoding="utf-8")
    requests = []

    async def query(prompt, options, role):  # ruff: ignore[unused-async] -- SDK boundary
        requests.append((prompt, options, role))
        if outcome == "error":
            raise RuntimeError("SDK unavailable")
        if outcome == "cancel":
            raise RunCancelled("Cancelled")
        if outcome == "missing":
            artifact.unlink()
        if outcome == "repaired":
            entries[1]["depends_on"] = ["service"]
            artifact.write_text(json.dumps({"tasks": entries}), encoding="utf-8")

    with (
        patch("meow.agents.base.Agent.run_query", side_effect=query),
        patch("meow.execution.workflow.builtin_steps.preflight", return_value=None),
        patch(
            "meow.execution.workflow.builtin_steps.run_parallel_plan", new=AsyncMock()
        ) as run,
    ):
        if outcome == "cancel":
            with pytest.raises(RunCancelled):
                asyncio.run(tasks(state))
        else:
            asyncio.run(tasks(state))

    assert len(requests) == 1
    prompt, options, role = requests[0]
    assert "independent tasks service and tests overlap" in prompt
    assert str(artifact) in options.system_prompt
    assert options.model == "pinned-planner"
    assert role == "Planner"
    assert state.plan_file.read_text(encoding="utf-8") == "Plan"
    if outcome == "repaired":
        assert state.flags["parallel"] is True
        graph = run.await_args.args[1]
        assert graph.tasks[1].depends_on == ("service",)
        assert state.store.load(state.run_id).results["task_graph_repaired"] is True
    else:
        run.assert_not_awaited()
        assert not state.flags.get("parallel")
        if outcome != "cancel":
            assert state.store.load(state.run_id).results["task_graph_error"]


def test_valid_task_graph_does_not_call_planner(tmp_path):
    state = state_for(tmp_path, plan=True)
    state.plan_file.with_suffix(".md.tasks.json").write_text(
        '{"tasks":[{"id":"service","depends_on":[],"owned_paths":["src"],"verification":[]}]}',
        encoding="utf-8",
    )
    with patch("meow.agents.base.Agent.run_query", new=AsyncMock()) as query:
        asyncio.run(tasks(state))
    query.assert_not_awaited()


def test_absent_task_graph_does_not_call_planner(tmp_path):
    state = state_for(tmp_path, plan=True)
    with patch("meow.agents.base.Agent.run_query", new=AsyncMock()) as query:
        asyncio.run(tasks(state))
    query.assert_not_awaited()


def test_plan_revision_edits_existing_plan_with_feedback(tmp_path):
    from meow.execution.workflow.builtin_steps import plan

    state = state_for(tmp_path, plan=True)
    state.round, state.feedback = 2, "SUMMARY: criterion untestable\nSTATUS: FAIL"
    state.sprint.config["models"]["planner"] = "pinned-planner"
    requests = []

    async def query(prompt, options, role):  # ruff: ignore[unused-async] -- SDK boundary
        requests.append((prompt, options, role))

    with patch("meow.agents.base.Agent.run_query", side_effect=query):
        asyncio.run(plan(state))

    prompt, options, role = requests[0]
    assert prompt == state.feedback
    assert f"Revise the existing plan at {state.plan_file}" in options.system_prompt
    assert "numbered task list" not in options.system_prompt
    assert options.model == "pinned-planner"
    assert role == "Planner"
