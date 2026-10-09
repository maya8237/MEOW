"""GitHub pull-request fetching for `meow review --github`."""

import json
import tempfile
from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.github_fetcher import GithubFetcherAgent
from meow.infrastructure.logging import get_logger

logger = get_logger(__name__)


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
    context = ProjectContext(working_dir, config)
    fetcher = GithubFetcherAgent(context, github_config["mcp"])

    logger.info("github_preflight_started")
    await fetcher.check_active()
    logger.info("github_preflight_finished")

    with tempfile.TemporaryDirectory() as tmp:
        output_file = Path(tmp) / "pull_request.json"
        logger.info("github_fetch_started", pr_link=pr_link)
        await fetcher.fetch(pr_link, output_file)
        data = json.loads(output_file.read_text(encoding="utf-8"))

    missing = [
        field
        for field in ("title", "description", "diff")
        if field not in data or data[field] is None
    ]
    if missing:
        raise RuntimeError(f"GitHub fetcher output is missing {missing}: {data}")
    logger.info("github_fetch_finished", title=data["title"])
    return data
