"""Shared double for the default workflow's plan-check gate."""

from unittest.mock import patch


async def _pass(agent, task, values, pool):  # ruff: ignore[unused-async] -- SDK double
    values["output_file"].write_text("SUMMARY: ok\nSTATUS: PASS", encoding="utf-8")


def plan_check_passes():
    """Patch the plan-checker stage so a mocked planner's plan passes the gate."""
    return patch("meow.execution.workflow.stages.StageAgent.run", side_effect=_pass)
