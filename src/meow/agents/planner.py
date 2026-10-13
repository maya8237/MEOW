"""Planner agent setup and sprint-plan generation."""

from pathlib import Path

from meow.agents.base import Agent
from meow.project.plan_files import planned_plan_file
from meow.project.prompts import planner_prompt, task_graph_repair_prompt
from meow.project.shaping import ShapeContext


def require_plan_file(plan_file: Path) -> Path:
    """Require the planner to leave a usable plan artifact behind."""
    if not plan_file.is_file():
        raise FileNotFoundError(f"Planner did not create plan file: {plan_file}")
    try:
        blank = not plan_file.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return plan_file  # cannot judge; the consumer reports a read failure
    if blank:
        raise RuntimeError(f"Planner left an empty plan file: {plan_file}")
    return plan_file


class PlannerAgent(Agent):
    """Generate a sprint plan through the configured planning model."""

    async def repair_task_graph(self, plan_file: Path, reason: str) -> None:
        """Ask the configured planner to repair the existing graph once."""
        agent = self.definition("planner")
        options = self.definition_options(
            agent, system_prompt=task_graph_repair_prompt(plan_file, agent=agent)
        )
        await self.run_definition_query(agent, reason, options, "Planner")

    async def run(
        self,
        feature_name: str | None,
        request: str,
        shape_context: ShapeContext | None = None,
    ) -> Path:
        docs_dir = self.context.active_working_dir() / self.context.config["docs_dir"]
        docs_dir.mkdir(parents=True, exist_ok=True)
        plan_file = planned_plan_file(docs_dir, feature_name)

        agent = self.definition("planner")
        options = self.definition_options(
            agent,
            system_prompt=planner_prompt(
                plan_file,
                shape_context,
                self.context.config.get("_preplan_context"),
                bug_mode=bool(self.context.config.get("_bug_mode", False)),
                agent=agent,
            ),
        )
        await self.run_definition_query(agent, request, options, "Planner")
        return require_plan_file(plan_file)
