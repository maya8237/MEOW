import asyncio
from unittest.mock import patch

import pytest

from meow.agents.base import Agent
from meow.agents.reviewer import ReviewerAgent
from meow.agents.tester import VerificationAgent
from meow.execution.run_state import RunStore
from meow.execution.sprint import Sprint
from meow.execution.workflow.engine import RunState
from meow.infrastructure.test_runner import VerificationStageEvidence
from meow.project.config import load_config


def saved_completion_run(tmp_path):
    request = "Prevent completing a task while any dependency is incomplete."
    plan = tmp_path / "plan.md"
    plan.write_text(
        "Provide a can_complete helper returning a boolean.", encoding="utf-8"
    )
    config = load_config(tmp_path)
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request=request, repo=tmp_path, worktree=tmp_path, branch="dev"
    )
    return RunState(
        sprint=Sprint(tmp_path, config, None, None),
        store=store,
        run_id=record.id,
        plan_file=plan,
        request=store.load(record.id).request,
    )


@pytest.mark.parametrize("role", ["reviewer", "tester"])
def test_weakened_plan_does_not_hide_original_completion_requirement(tmp_path, role):
    state = saved_completion_run(tmp_path)
    assert state.sprint.original_request == state.request
    captured = []

    async def query(message, options, label):  # ruff: ignore[unused-async] -- SDK double
        captured.append((message, options.system_prompt))

    with patch.object(Agent, "run_query", side_effect=query):
        if role == "reviewer":
            status, _ = asyncio.run(
                ReviewerAgent(state.sprint).review_plan(state.plan_file)
            )
        else:
            status, _ = asyncio.run(
                VerificationAgent(state.sprint).test_plan(
                    state.plan_file, VerificationStageEvidence()
                )
            )
    assert status == "FAIL"  # No fresh evidence/report may yield PASS.
    message, instructions = captured[0]
    assert state.request in message
    assert "original request" in instructions.lower()
    assert "omitted or weakened" in instructions
    assert "each acceptance criterion" in instructions
    assert "remain unchanged" in instructions
    assert "helper" in instructions
