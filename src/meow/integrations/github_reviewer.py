"""GitHub pull-request fetching for `meow review --github`."""

from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.mcp_fetcher import GithubFetcherAgent, fetch_record


def _load_github_config(config: dict) -> dict:
    """Resolve this provider only when requested."""
    from meow.project.mcp_registry import provider_mcp_config

    mcp = provider_mcp_config(config, "github")
    return {"mcp": {**mcp, "env": mcp.get("env", {})}}


async def _fetch_pull_request(
    working_dir: Path, config: dict, github_config: dict, pr_link: str
) -> dict:
    fetcher = GithubFetcherAgent(
        ProjectContext(working_dir, config), github_config["mcp"]
    )
    return await fetch_record(
        fetcher,
        lambda output_file: fetcher.fetch(pr_link, output_file),
        ("title", "description", "diff"),
    )
