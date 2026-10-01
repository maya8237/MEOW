"""
meow/issue_solver.py

The `meow run --jira` flow: fetch a Jira issue (a given key, or the most
recently created one in a configured project), then solve it through the
same generator<->reviewer loop plain `meow run` uses (`sprint_runner.run_sprint`),
inside a dedicated, named-branch worktree that gets pushed on success.

Unlike `run`/`plan`'s worktrees (created detached, never pushed), this flow
always needs a real branch to hand back to the caller, so `worktree.py`
creates its own worktree on one instead of the usual detached one.
"""

import json
import re
import tempfile
from collections.abc import Callable
from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.issue_fetcher import IssueFetcherAgent
from meow.config import load_config
from meow.logging import get_logger
from meow.sprint_runner import run_sprint
from meow.worktree import _ensure_branch_worktree, _push_branch

logger = get_logger(__name__)

DEFAULT_BRANCH_PREFIX = "issue/"
_BRANCH_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class IssueUnresolvedError(RuntimeError):
    """`meow run --jira` could not fetch, solve, or push the issue."""


def _load_jira_config(config: dict) -> dict:
    """Validate `[jira]`/`[jira.mcp]` up front, with a clear error if absent."""
    jira = config.get("jira")
    if not isinstance(jira, dict):
        raise ValueError(
            "No [jira] table found in .harness.toml. `meow run --jira` needs "
            "[jira] with 'project_key' set, and a [jira.mcp] table "
            "describing how to launch the Jira MCP server, e.g.:\n\n"
            "[jira]\n"
            'project_key = "PROJ"\n\n'
            "[jira.mcp]\n"
            'command = "uvx"\n'
            'args = ["mcp-atlassian"]\n\n'
            "See docs/INTEGRATIONS.md for the Jira credentials the server "
            "itself needs in the environment (JIRA_URL, JIRA_USERNAME, "
            "JIRA_API_TOKEN, or JIRA_PERSONAL_TOKEN)."
        )

    project_key = jira.get("project_key")
    if not project_key:
        raise ValueError(
            "[jira] in .harness.toml must set 'project_key' -- the project "
            "`meow run --jira` searches for the latest issue when none is given."
        )

    mcp = jira.get("mcp")
    if not isinstance(mcp, dict) or not mcp.get("command"):
        raise ValueError(
            "[jira.mcp] in .harness.toml must set 'command' (the program "
            'that launches the Jira MCP server, e.g. command = "uvx" with '
            'args = ["mcp-atlassian"]).'
        )

    return {
        "project_key": project_key,
        "branch_prefix": jira.get("branch_prefix", DEFAULT_BRANCH_PREFIX),
        "mcp": {"command": mcp["command"], "args": mcp.get("args", [])},
    }


def _sanitize(component: str) -> str:
    return _BRANCH_UNSAFE.sub("-", component).strip("-")


async def _fetch_issue(
    working_dir: Path, config: dict, jira_config: dict, issue_key: str | None
) -> dict:
    context = ProjectContext(working_dir, config)
    fetcher = IssueFetcherAgent(context, jira_config["mcp"])

    logger.info("jira_preflight_started")
    await fetcher.check_active()
    logger.info("jira_preflight_finished")

    with tempfile.TemporaryDirectory() as tmp:
        output_file = Path(tmp) / "issue.json"
        logger.info(
            "jira_fetch_started",
            issue=issue_key or f"latest in {jira_config['project_key']}",
        )
        await fetcher.fetch(issue_key, jira_config["project_key"], output_file)
        data = json.loads(output_file.read_text(encoding="utf-8"))

    missing = [
        field
        for field in ("key", "summary", "description")
        if field not in data or data[field] is None
    ]
    if missing:
        raise RuntimeError(f"Jira issue-fetcher output is missing {missing}: {data}")
    logger.info("jira_fetch_finished", key=data["key"])
    return data


async def run_issue_solver(
    working_dir: Path,
    issue_key: str | None = None,
    *,
    approve_plan: Callable[[Path], bool] | None = None,
) -> dict:
    """Fetch a Jira issue, solve it in a worktree, push the branch, return it.

    Returns `{"issue": <key>, "branch": <branch name>}` on success. Raises on
    any failure (no active Jira MCP, no matching issue, the plan being
    declined when `approve_plan` is given, the sprint not passing within
    max_rounds, or the push failing) -- there is no partial "best effort"
    result. `approve_plan` is passed straight through to `run_sprint`; see
    its docstring.
    """
    config = load_config(working_dir)
    jira_config = _load_jira_config(config)

    try:
        return await _solve_issue(
            working_dir, config, jira_config, issue_key, approve_plan
        )
    except Exception as exc:
        logger.warning(
            "issue_unresolved", issue=issue_key or "latest", reason=str(exc)
        )
        raise IssueUnresolvedError(str(exc)) from exc


async def _solve_issue(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- reducing args would change run_issue_solver's call site
    working_dir: Path,
    config: dict,
    jira_config: dict,
    issue_key: str | None,
    approve_plan: Callable[[Path], bool] | None,
) -> dict:
    issue = await _fetch_issue(working_dir, config, jira_config, issue_key)

    sanitized_key = _sanitize(issue["key"])
    feature_name = f"issue-{sanitized_key}".lower()
    branch_name = f"{jira_config['branch_prefix']}{issue['key']}"

    active_dir = _ensure_branch_worktree(working_dir, feature_name, branch_name)
    request = (
        f"Resolve Jira issue {issue['key']}: {issue['summary']}\n\n"
        f"{issue['description']}"
    )

    logger.info("issue_solver_sprint_started", issue=issue["key"], branch=branch_name)
    await run_sprint(
        active_dir,
        feature_name,
        request,
        use_worktree=False,
        approve_plan=approve_plan,
    )
    logger.info("issue_solver_sprint_finished", issue=issue["key"], branch=branch_name)

    _push_branch(active_dir, branch_name)
    logger.info("issue_solver_pushed", issue=issue["key"], branch=branch_name)

    return {"issue": issue["key"], "branch": branch_name}
