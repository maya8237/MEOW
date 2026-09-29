"""
meow/gitlab_reviewer.py

The `meow gitlab-review` flow: fetch a GitLab merge request's title,
description, and diff through a configured GitLab MCP server, then grade
them with the reviewer role -- the same report-only, no-generator-loop
review `orchestrator.run_prompt_review` runs against a local working tree,
but against a remote merge request's diff instead.

Unlike `meow issue`, this never edits code, creates a worktree, or pushes
anything -- it is read-only, and `--working-dir` selects only where the
verdict file is written, not code that gets changed.
"""

import json
import tempfile
from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.gitlab_fetcher import GitlabFetcherAgent
from meow.agents.reviewer import MR_REVIEW_FILENAME, ReviewerAgent
from meow.config import load_config
from meow.logging import get_logger

logger = get_logger(__name__)


def _load_gitlab_config(config: dict) -> dict:
    """Validate `[gitlab]`/`[gitlab.mcp]` up front, with a clear error if absent."""
    gitlab = config.get("gitlab")
    if not isinstance(gitlab, dict):
        raise ValueError(
            "No [gitlab] table found in .harness.toml. `meow gitlab-review` "
            "needs a [gitlab.mcp] table describing how to launch a GitLab "
            "MCP server, e.g.:\n\n"
            "[gitlab.mcp]\n"
            'command = "uvx"\n'
            'args = ["mcp-gitlab"]\n\n'
            "See GUIDE.md for the GitLab credentials the server itself "
            "needs in the environment."
        )

    mcp = gitlab.get("mcp")
    if not isinstance(mcp, dict) or not mcp.get("command"):
        raise ValueError(
            "[gitlab.mcp] in .harness.toml must set 'command' (the program "
            'that launches the GitLab MCP server, e.g. command = "uvx" '
            'with args = ["mcp-gitlab"]).'
        )

    return {"mcp": {"command": mcp["command"], "args": mcp.get("args", [])}}


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
        field for field in ("title", "description", "diff") if not data.get(field)
    ]
    if missing:
        raise RuntimeError(
            f"GitLab fetcher output is missing {missing}: {data}"
        )
    logger.info("gitlab_fetch_finished", title=data["title"])
    return data


async def run_gitlab_review(working_dir: Path, mr_link: str) -> None:
    """Fetch a GitLab merge request's diff and grade it, PASS/FAIL.

    Reports the verdict the same way `run_prompt_review` does -- logged and
    written to a review file -- rather than raising on FAIL; there is no
    Sprint Contract or generator loop to gate on here.
    """
    config = load_config(working_dir)
    gitlab_config = _load_gitlab_config(config)
    context = ProjectContext(working_dir, config)

    mr = await _fetch_merge_request(working_dir, config, gitlab_config, mr_link)

    logger.info("gitlab_review_started", mr_link=mr_link, title=mr["title"])
    status, _ = await ReviewerAgent(context).review_merge_request(
        mr["title"], mr["description"], mr["diff"]
    )
    review_file = context.active_working_dir() / config["docs_dir"] / MR_REVIEW_FILENAME
    logger.info("gitlab_review_finished", status=status, review_file=str(review_file))
