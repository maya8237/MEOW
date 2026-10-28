"""`meow review --gitlab`: validate `[gitlab.mcp]` and fetch a merge
request's title, description and diff through it (read-only)."""

from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.mcp_fetcher import GitlabFetcherAgent, fetch_record


def _load_gitlab_config(config: dict) -> dict:
    """Resolve this provider only when requested."""
    from meow.project.mcp_registry import provider_mcp_config

    mcp = provider_mcp_config(config, "gitlab")
    return {"mcp": {**mcp, "env": mcp.get("env", {})}}


async def _fetch_merge_request(
    working_dir: Path, config: dict, gitlab_config: dict, mr_link: str
) -> dict:
    fetcher = GitlabFetcherAgent(
        ProjectContext(working_dir, config), gitlab_config["mcp"]
    )
    return await fetch_record(
        fetcher,
        lambda output_file: fetcher.fetch(mr_link, output_file),
        ("title", "description", "diff"),
    )
