"""Planner agent setup and sprint-plan generation."""

from pathlib import Path

from meow.agents.base import Agent
from meow.agents.explorer import ExplorerAgent
from meow.prompts import planner_prompt
from meow.sprint import Sprint


class PlannerAgent(Agent):
    """Generate a sprint plan through the configured planning model."""

    async def run(self, feature_name: str | None, request: str) -> Path:
        active_dir = (
            self.context.active_working_dir() / self.context.config["docs_dir"]
        )
        active_dir.mkdir(parents=True, exist_ok=True)
        plan_filename = f"{feature_name}.md" if feature_name else "plan.md"
        plan_file = active_dir / plan_filename

        options = self.options(
            system_prompt=planner_prompt(plan_file),
            allowed_tools=["Read", "Grep", "Glob", "Write", "Agent"],
            role="planner",
            agents={"explorer": ExplorerAgent(self.context).definition()},
            skills=["superpowers:writing-plans"],
        )
        await self.run_query(request, options, "Planner")
        return plan_file


async def run_planner(sprint: Sprint, feature_name: str | None, request: str) -> Path:
    return await PlannerAgent(sprint).run(feature_name, request)
