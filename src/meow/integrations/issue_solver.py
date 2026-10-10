"""The `meow run --jira` flow: fetch a Jira issue (a given key, or the most
recently created one in a configured project), then solve it through the
same generator<->reviewer loop plain `meow run` uses (`sprint_runner.run_sprint`),
inside a dedicated, named-branch worktree. A verified run commits and pushes
the branch to origin.

Unlike `run`/`plan`'s initially detached worktrees, this flow starts on a
named branch so `worktree.py`
creates its own worktree on one instead of the usual detached one.
"""

from collections.abc import Callable
from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.mcp_fetcher import IssueFetcherAgent, fetch_record
from meow.execution.run_state import RunStore
from meow.execution.sprint_runner import run_sprint
from meow.infrastructure.cancellation import cancellable
from meow.infrastructure.logging import get_logger
from meow.infrastructure.worktree import _ensure_branch_worktree, sanitize_name
from meow.project.config import load_config

logger = get_logger(__name__)

DEFAULT_BRANCH_PREFIX = "issue/"


class IssueUnresolvedError(RuntimeError):
    """`meow run --jira` could not fetch or solve the issue."""


def _load_jira_config(config: dict) -> dict:
    """Validate `[jira]`/`[jira.mcp]` up front, with a clear error if absent."""
    jira = config.get("jira")
    if not isinstance(jira, dict):
        raise ValueError(
            "No [jira] table found in .meow/config.toml. `meow run --jira` needs "
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
            "[jira] in .meow/config.toml must set 'project_key' -- the project "
            "`meow run --jira` searches for the latest issue when none is given."
        )

    mcp = jira.get("mcp")
    if not isinstance(mcp, dict) or not mcp.get("command"):
        raise ValueError(
            "[jira.mcp] in .meow/config.toml must set 'command' (the program "
            'that launches the Jira MCP server, e.g. command = "uvx" with '
            'args = ["mcp-atlassian"]).'
        )

    return {
        "project_key": project_key,
        "branch_prefix": jira.get("branch_prefix", DEFAULT_BRANCH_PREFIX),
        "mcp": {"command": mcp["command"], "args": mcp.get("args", [])},
    }


async def _fetch_issue(
    working_dir: Path, config: dict, jira_config: dict, issue_key: str | None
) -> dict:
    fetcher = IssueFetcherAgent(ProjectContext(working_dir, config), jira_config["mcp"])
    return await fetch_record(
        fetcher,
        lambda output_file: fetcher.fetch(
            issue_key, jira_config["project_key"], output_file
        ),
        ("key", "summary", "description"),
    )


async def run_issue_solver(  # ruff: ignore[too-many-arguments]
    working_dir: Path,
    issue_key: str | None = None,
    *,
    approve_plan: Callable[[Path], bool] | None = None,
    test: bool = False,
    run_id: str | None = None,
) -> dict:
    """Fetch a Jira issue, solve it in a worktree, and return its branch.

    Returns `{"issue": <key>, "branch": <branch name>}` on success. Raises on
    any failure (no active Jira MCP, no matching issue, the plan being
    declined when `approve_plan` is given, or the sprint not passing within
    max_rounds) -- there is no partial "best effort"
    result. `approve_plan` is passed straight through to `run_sprint`; see
    its docstring.
    """
    config = load_config(working_dir)
    jira_config = _load_jira_config(config)

    try:
        return await _solve_issue(
            working_dir,
            config,
            jira_config,
            issue_key,
            approve_plan,
            run_id=run_id,
            **({"test": True} if test else {}),
        )
    except Exception as exc:
        logger.warning("issue_unresolved", issue=issue_key or "latest", reason=str(exc))
        raise IssueUnresolvedError(str(exc)) from exc


async def _solve_issue(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- reducing args would change run_issue_solver's call site
    working_dir: Path,
    config: dict,
    jira_config: dict,
    issue_key: str | None,
    approve_plan: Callable[[Path], bool] | None,
    test: bool = False,
    run_id: str | None = None,
) -> dict:
    issue = (
        await cancellable(
            RunStore(working_dir),
            run_id,
            _fetch_issue(working_dir, config, jira_config, issue_key),
        )
        if run_id
        else await _fetch_issue(working_dir, config, jira_config, issue_key)
    )

    sanitized_key = sanitize_name(issue["key"])
    feature_name = f"issue-{sanitized_key}".lower()
    branch_name = f"{jira_config['branch_prefix']}{issue['key']}"

    existing_worktree = (working_dir / ".worktrees" / feature_name).exists()
    active_dir = _ensure_branch_worktree(working_dir, feature_name, branch_name)
    request = (
        f"Resolve Jira issue {issue['key']}: {issue['summary']}\n\n"
        f"{issue['description']}"
    )
    if run_id:
        RunStore(working_dir).transition(
            run_id,
            "issue_fetched",
            request=request,
            worktree=str(active_dir),
            branch=branch_name,
        )

    logger.info("issue_solver_sprint_started", issue=issue["key"], branch=branch_name)
    run_options = {"run_id": run_id, "unattended": True} if run_id else {}
    if existing_worktree:
        run_options["worktree_preexisting"] = True
    await run_sprint(
        active_dir,
        feature_name,
        request,
        use_worktree=False,
        approve_plan=approve_plan,
        record_root=working_dir,
        source="jira",
        **run_options,
        **({"test": True} if test else {}),
    )
    logger.info("issue_solver_sprint_finished", issue=issue["key"], branch=branch_name)

    return {"issue": issue["key"], "branch": branch_name}
