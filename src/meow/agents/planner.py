"""Planner agent setup and sprint-plan generation."""

from pathlib import Path

from meow.agents.base import Agent
from meow.agents.explorer import ExplorerAgent
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
            system_prompt=(
                "You are a planning agent. Consult the explorer subagent for "
                "any codebase context you need -- don't explore directly. "
                "Produce a numbered task list with acceptance criteria per "
                "task, plus a proposed '## Sprint Contract' section with "
                f"concrete, testable pass/fail criteria. Write the result to "
                f"{plan_file}. Do not write application code. Apply the "
                "writing-plans skill for task sizing, file mapping, testability, "
                "and self-review. MEOW-specific requirements take precedence: "
                "use the configured plan path, retain this Sprint Contract, "
                "and do not use the skill's default plan location or add an "
                "interactive execution-method handoff."
            ),
            allowed_tools=["Read", "Grep", "Glob", "Write", "Agent"],
            role="planner",
            agents={"explorer": ExplorerAgent(self.context).definition()},
            skills=["superpowers:writing-plans"],
        )
        await self.run_query(request, options, "Planner")
        return plan_file


async def run_planner(sprint: Sprint, feature_name: str | None, request: str) -> Path:
    return await PlannerAgent(sprint).run(feature_name, request)
