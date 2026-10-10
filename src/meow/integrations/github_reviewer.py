"""GitHub pull-request fetching for `meow review --github`."""

from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.mcp_fetcher import GithubFetcherAgent, fetch_record


def _load_github_config(config: dict) -> dict:
    """Validate `[github]`/`[github.mcp]` before starting an agent."""
    github = config.get("github")
    if not isinstance(github, dict):
        raise ValueError(
            "No [github] table found in .meow/config.toml. `meow review "
            "--github` needs a [github.mcp] table describing how to launch "
            "a GitHub MCP server, e.g.:\n\n"
            "[github.mcp]\n"
            'command = "npx"\n'
            'args = ["your-github-mcp-package"]\n\n'
            "[github.mcp.env]\n"
            'GITHUB_PERSONAL_ACCESS_TOKEN = "<token>"\n\n'
            "See docs/INTEGRATIONS.md for the [github.mcp.env] field and its "
            "security note."
        )

    mcp = github.get("mcp")
    if not isinstance(mcp, dict) or not mcp.get("command"):
        raise ValueError(
            "[github.mcp] in .meow/config.toml must set 'command' (the "
            "program that launches the GitHub MCP server, with args for the "
            "server and env for its credentials)."
        )

    return {
        "mcp": {
            "command": mcp["command"],
            "args": mcp.get("args", []),
            "env": mcp.get("env", {}),
        }
    }


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
