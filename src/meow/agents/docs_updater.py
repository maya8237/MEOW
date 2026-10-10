"""Agent for explicit, evidence-bounded documentation maintenance."""

from pathlib import Path

from meow.agents.base import Agent, ProjectContext, guard_tools, tool_input_path
from meow.integrations.docs_update import DocsUpdateInput
from meow.project.config import load_config
from meow.project.prompts import docs_update_prompt


def _allowed_edit_path(repo: Path, tool_input: dict) -> bool:
    target = tool_input_path(tool_input, repo)
    try:
        relative = target.relative_to(repo.resolve()) if target else None
    except ValueError:
        return False
    if relative is None:
        return False
    return relative.as_posix() == "README.md" or (
        bool(relative.parts)
        and relative.parts[0] == "docs"
        and relative.suffix.lower() in {".md", ".rst", ".txt"}
    )


async def update_documentation(prepared: DocsUpdateInput) -> None:
    config = load_config(prepared.repo)
    options = Agent(ProjectContext(prepared.repo, config)).options(
        system_prompt=docs_update_prompt(prepared.baseline_sha, prepared.head_sha),
        allowed_tools=["Read", "Grep", "Glob", "Edit", "Write"],
        role="docs_updater",
    )
    guard_tools(
        options,
        {"Edit", "Write"},
        lambda tool_input: (
            None
            if _allowed_edit_path(prepared.repo, tool_input)
            else "Docs updater may edit prose only"
        ),
    )
    await Agent.run_query(
        "Changed paths: "
        + ", ".join(prepared.changed_paths)
        + "\n\nGit diff:\n"
        + prepared.diff,
        options,
        "DocsUpdater",
    )
