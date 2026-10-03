"""
meow/gitlab_reviewer.py

GitLab MR config loading and fetching, used by `review_cli.py`'s
`--gitlab` review source (`meow review --gitlab <mr-link>`): fetch a
merge request's title, description, and diff through a configured GitLab
MCP server, then grade them with the reviewer role -- read-only, since
this never checks the merge request's code out locally.
"""

import json
import tempfile
from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.gitlab_fetcher import GitlabFetcherAgent
from meow.infrastructure.logging import get_logger

logger = get_logger(__name__)


def _load_gitlab_config(config: dict) -> dict:
    """Validate `[gitlab]`/`[gitlab.mcp]` up front, with a clear error if absent.

    Unlike `[jira.mcp]` (whose server reads its own credentials from the
    environment), `[gitlab.mcp.env]` is read directly from `.harness.toml`
    and passed straight through as the launched server's environment --
    see the security note on `[gitlab.mcp.env]` in
    `templates/harness.toml.example` before putting a real credential
    there: `.harness.toml` is an ordinary, typically-committed project
    file, not a secrets store.
    """
    gitlab = config.get("gitlab")
    if not isinstance(gitlab, dict):
        raise ValueError(
            "No [gitlab] table found in .harness.toml. `meow review "
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
            "[gitlab.mcp] in .harness.toml must set 'command' (the program "
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
    context = ProjectContext(working_dir, config)
    fetcher = GitlabFetcherAgent(context, gitlab_config["mcp"])

    logger.info("gitlab_preflight_started")
    await fetcher.check_active()
    logger.info("gitlab_preflight_finished")

    with tempfile.TemporaryDirectory() as tmp:
        output_file = Path(tmp) / "merge_request.json"
        logger.info("gitlab_fetch_started", mr_link=mr_link)
        await fetcher.fetch(mr_link, output_file)
        data = json.loads(output_file.read_text(encoding="utf-8"))

    missing = [
        field
        for field in ("title", "description", "diff")
        if field not in data or data[field] is None
    ]
    if missing:
        raise RuntimeError(f"GitLab fetcher output is missing {missing}: {data}")
    logger.info("gitlab_fetch_finished", title=data["title"])
    return data
