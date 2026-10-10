"""Planner agent setup and sprint-plan generation."""

from pathlib import Path

from meow.agents.base import Agent
from meow.agents.explorer import ExplorerAgent
from meow.execution.sprint import Sprint
from meow.project.prompts import planner_prompt
from meow.project.shaping import ShapeContext


class PlannerAgent(Agent):
    """Generate a sprint plan through the configured planning model."""

    async def run(
        self,
        feature_name: str | None,
        request: str,
        shape_context: ShapeContext | None = None,
    ) -> Path:
        active_dir = self.context.active_working_dir() / self.context.config["docs_dir"]
        active_dir.mkdir(parents=True, exist_ok=True)
        # Keep in step with plan_files.planned_plan_file (importing it here
        # would be circular: plan_files imports the reviewer agent).
        plan_filename = f"{feature_name}.md" if feature_name else "plan.md"
        plan_file = active_dir / plan_filename

        options = self.options(
            system_prompt=planner_prompt(
                plan_file,
                shape_context,
                self.context.config.get("_preplan_context"),
                bug_mode=bool(self.context.config.get("_bug_mode", False)),
            ),
            allowed_tools=["Read", "Grep", "Glob", "Write", "Agent"],
            role="planner",
            agents={"explorer": ExplorerAgent(self.context).definition()},
            skills=["superpowers:writing-plans"],
        )
        await self.run_query(request, options, "Planner")
        return plan_file


async def run_planner(
    sprint: Sprint,
    feature_name: str | None,
    request: str,
    shape_context: ShapeContext | None = None,
) -> Path:
    return await PlannerAgent(sprint).run(feature_name, request, shape_context)
