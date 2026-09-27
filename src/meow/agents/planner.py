"""Planner agent setup and sprint-plan generation."""

from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from meow.sprint import Sprint


async def run_planner(sprint: Sprint, feature_name: str | None, request: str) -> Path:
    active_dir = sprint.active_working_dir() / sprint.config["docs_dir"]
    active_dir.mkdir(parents=True, exist_ok=True)
    plan_filename = f"{feature_name}.md" if feature_name else "plan.md"
    plan_file = active_dir / plan_filename

    options = ClaudeAgentOptions(
        system_prompt=(
            "You are a planning agent. Consult the explorer subagent for "
            "any codebase context you need -- don't explore directly. "
            "Produce a numbered task list with acceptance criteria per "
            "task, plus a proposed '## Sprint Contract' section with "
            f"concrete, testable pass/fail criteria. Write the result to "
            f"{plan_file}. Do not write application code."
        ),
        allowed_tools=["Read", "Grep", "Glob", "Write", "Agent"],
        agents={"explorer": sprint.explorer},
        model=sprint.model("planner"),
        cwd=str(sprint.active_working_dir()),
    )

    async for message in query(prompt=request, options=options):
        if isinstance(message, ResultMessage) and message.subtype != "success":
            raise RuntimeError(f"Planner failed: {message.subtype}")

    return plan_file
