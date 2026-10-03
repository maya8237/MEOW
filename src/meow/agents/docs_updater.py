"""Agent for explicit, evidence-bounded documentation maintenance."""

from collections.abc import Callable
from pathlib import Path

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from meow.agents.base import Agent, ProjectContext
from meow.config import load_config
from meow.docs_update.core import DocsUpdateInput
from meow.prompts import docs_update_prompt


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


def _docs_only_callback(repo: Path, project_callback: Callable | None):
    async def docs_only(tool: str, tool_input: dict, context: object):
        if tool in {"Edit", "Write"} and not _allowed_edit_path(
            repo, tool_input.get("file_path") or tool_input.get("path")
        ):
            return PermissionResultDeny(message="Docs updater may edit prose only")
        if project_callback is not None:
            return await project_callback(tool, tool_input, context)
        return PermissionResultAllow()

    return docs_only


async def update_documentation(prepared: DocsUpdateInput) -> None:
    config = load_config(prepared.repo)
    options = Agent(ProjectContext(prepared.repo, config)).options(
        system_prompt=docs_update_prompt(prepared.baseline_sha, prepared.head_sha),
        allowed_tools=["Read", "Grep", "Glob", "Edit", "Write"],
        role="docs_updater",
    )
    options.can_use_tool = _docs_only_callback(prepared.repo, options.can_use_tool)
    await Agent.run_query(
        "Changed paths: "
        + ", ".join(prepared.changed_paths)
        + "\n\nGit diff:\n"
        + prepared.diff,
        options,
        "DocsUpdater",
    )
