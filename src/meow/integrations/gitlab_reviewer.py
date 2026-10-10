"""`meow review --gitlab`: validate `[gitlab.mcp]` and fetch a merge
request's title, description and diff through it (read-only)."""

from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.mcp_fetcher import GitlabFetcherAgent, fetch_record


def _load_gitlab_config(config: dict) -> dict:
    """Validate `[gitlab]`/`[gitlab.mcp]` up front, with a clear error if absent.

    Unlike `[jira.mcp]` (whose server reads its own credentials from the
    environment), `[gitlab.mcp.env]` is passed straight through as the launched
    server's environment -- see the security note on `[gitlab.mcp.env]` in
    `templates/meow-config.toml.example` before putting a real credential
    there: `.meow/config.toml` is an ordinary, typically-committed project
    file, not a secrets store.
    """
    gitlab = config.get("gitlab")
    if not isinstance(gitlab, dict):
        raise ValueError(
            "No [gitlab] table found in .meow/config.toml. `meow review "
            "--gitlab` needs a [gitlab.mcp] table describing how to "
            "launch a GitLab MCP server, e.g.:\n\n"
            "[gitlab.mcp]\n"
            'command = "uvx"\n'
            'args = ["mcp-gitlab"]\n\n'
            "[gitlab.mcp.env]\n"
            'GITLAB_URL = "https://gitlab.example.com"\n'
            'GITLAB_TOKEN = "<token>"\n\n'
            "See docs/INTEGRATIONS.md for the [gitlab.mcp.env] field and its "
            "security note."
        )

    mcp = gitlab.get("mcp")
    if not isinstance(mcp, dict) or not mcp.get("command"):
        raise ValueError(
            "[gitlab.mcp] in .meow/config.toml must set 'command' (the program "
            'that launches the GitLab MCP server, e.g. command = "uvx" '
            'with args = ["mcp-gitlab"]).'
        )

    return {
        "mcp": {
            "command": mcp["command"],
            "args": mcp.get("args", []),
            "env": mcp.get("env", {}),
        }
    }


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
