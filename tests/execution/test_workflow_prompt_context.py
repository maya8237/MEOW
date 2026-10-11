import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from meow.execution.run_state import RunStore
from meow.execution.sprint import Sprint
from meow.execution.sprint_runner import run_sprint
from meow.execution.workflow.engine import GateFailed, RunState, run_workflow
from meow.project.config import load_config
from meow.project.custom.workflows import Step
from meow.project.plan_files import planned_plan_file


def test_builtin_planner_receives_workflow_inputs_and_flags(tmp_path):
    directory = tmp_path / ".meow"
    (directory / "agents").mkdir(parents=True)
    (directory / "workflows").mkdir()
    (directory / "agents" / "planner.toml").write_text(
        'description="Plan"\nprompt="Plan {{inputs.topic}} test={{test}}"\n'
        'tools=["Write"]',
        encoding="utf-8",
    )
    (directory / "workflows" / "plan-only.toml").write_text(
        'description="Plan"\nsteps=[{uses="builtin.plan"}]', encoding="utf-8"
    )

    async def query(self, agent, prompt, options, *args):  # ruff: ignore[unused-async] -- SDK double
        assert options.system_prompt == "Plan API test=True"
        output = planned_plan_file(tmp_path / ".meow" / "plans", None)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("Plan", encoding="utf-8")

    with patch("meow.agents.planner.PlannerAgent.run_definition_query", new=query):
        asyncio.run(
            run_sprint(
                tmp_path,
                None,
                "Plan",
                use_worktree=False,
                workflow="plan-only",
                inputs={"topic": "API"},
                test=True,
            )
        )


def test_loop_gate_outside_loop_fails_closed(tmp_path):
    config = load_config(tmp_path)
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="Audit", repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    state = RunState(Sprint(tmp_path, config, None, None), store, record.id)
    with (
        patch(
            "meow.execution.workflow.stages.StageAgent.run",
            new=AsyncMock(return_value=""),
        ),
        pytest.raises(GateFailed),
    ):
        asyncio.run(
            run_workflow(
                state, (Step("gate", "audit", target="reviewer", blocks="loop"),)
            )
        )
