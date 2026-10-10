"""Agent for explicit, evidence-bounded documentation maintenance."""

from pathlib import Path

from meow.agents.base import Agent, ProjectContext, guard_tools
from meow.integrations.docs_update import DocsUpdateInput
from meow.project.config import load_config
from meow.project.prompts import docs_update_prompt


def _allowed_edit_path(repo: Path, raw_path: object) -> bool:
    if not isinstance(raw_path, str):
        return False
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = repo / candidate
    try:
        relative = candidate.resolve().relative_to(repo.resolve())
    except ValueError:
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
        lambda tool_input: None
        if _allowed_edit_path(
            prepared.repo, tool_input.get("file_path") or tool_input.get("path")
        )
        else "Docs updater may edit prose only",
    )
    await Agent.run_query(
        "Changed paths: "
        + ", ".join(prepared.changed_paths)
        + "\n\nGit diff:\n"
        + prepared.diff,
        options,
        "DocsUpdater",
    )
