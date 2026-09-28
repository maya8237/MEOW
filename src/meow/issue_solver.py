"""
meow/issue_solver.py

The `issue-solver` flow: fetch a Jira issue (a given key, or the most
recently created one in a configured project), then solve it through the
same generator<->reviewer loop `meow run` uses (`orchestrator.run_sprint`),
inside a dedicated, named-branch worktree that gets pushed on success.

Unlike `run`/`plan`'s worktrees (created detached, never pushed), this flow
always needs a real branch to hand back to the caller, so it manages its own
worktree instead of going through `orchestrator._ensure_feature_worktree`.
"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.issue_fetcher import IssueFetcherAgent
from meow.config import load_config
from meow.logging import get_logger
from meow.orchestrator import run_sprint

logger = get_logger(__name__)

DEFAULT_BRANCH_PREFIX = "issue/"
_BRANCH_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _load_jira_config(config: dict) -> dict:
    """Validate `[jira]`/`[jira.mcp]` up front, with a clear error if absent."""
    jira = config.get("jira")
    if not isinstance(jira, dict):
        raise ValueError(
            "No [jira] table found in .harness.toml. issue-solver needs "
            "[jira] with 'project_key' set, and a [jira.mcp] table "
            "describing how to launch the Jira MCP server, e.g.:\n\n"
            "[jira]\n"
            'project_key = "PROJ"\n\n'
            "[jira.mcp]\n"
            'command = "uvx"\n'
            'args = ["mcp-atlassian"]\n\n'
            "See GUIDE.md for the Jira credentials the server itself needs "
            "in the environment (JIRA_URL, JIRA_USERNAME, JIRA_API_TOKEN, "
            "or JIRA_PERSONAL_TOKEN)."
        )

    project_key = jira.get("project_key")
    if not project_key:
        raise ValueError(
            "[jira] in .harness.toml must set 'project_key' -- the project "
            "issue-solver searches for the latest issue when none is given."
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
        field for field in ("key", "summary", "description") if not data.get(field)
    ]
    if missing:
        raise RuntimeError(f"Jira issue-fetcher output is missing {missing}: {data}")
    logger.info("jira_fetch_finished", key=data["key"])
    return data


def _run_git(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)


def _ensure_branch_worktree(
    working_dir: Path, feature_name: str, branch_name: str
) -> Path:
    """Create (or reuse) a worktree checked out on a real, pushable branch."""
    worktree_dir = working_dir / ".worktrees" / feature_name
    if worktree_dir.exists():
        return worktree_dir

    git = shutil.which("git")
    if not git:
        raise RuntimeError(
            "git is required for issue-solver's worktree/branch/push steps."
        )

    (working_dir / ".worktrees").mkdir(parents=True, exist_ok=True)
    branch_exists = _run_git(
        [git, "rev-parse", "--verify", "--quiet", branch_name], cwd=working_dir
    ).returncode == 0

    if branch_exists:
        argv = [git, "worktree", "add", str(worktree_dir), branch_name]
    else:
        argv = [git, "worktree", "add", "-b", branch_name, str(worktree_dir)]
    result = _run_git(argv, cwd=working_dir)
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not create worktree for branch '{branch_name}':\n{result.stderr}"
        )
    return worktree_dir


def _push_branch(worktree_dir: Path, branch_name: str) -> None:
    git = shutil.which("git")
    remotes = _run_git([git, "remote"], cwd=worktree_dir).stdout.split()
    if "origin" not in remotes:
        raise RuntimeError(
            "No 'origin' remote configured -- issue-solver can't push the "
            f"finished branch '{branch_name}'. Add one with `git remote add "
            "origin <url>`, or push it yourself."
        )

    result = _run_git([git, "push", "-u", "origin", branch_name], cwd=worktree_dir)
    if result.returncode != 0:
        raise RuntimeError(
            f"git push failed for branch '{branch_name}':\n{result.stderr}"
        )


async def run_issue_solver(working_dir: Path, issue_key: str | None = None) -> dict:
    """Fetch a Jira issue, solve it in a worktree, push the branch, return it.

    Returns `{"issue": <key>, "branch": <branch name>}` on success. Raises on
    any failure (no active Jira MCP, no matching issue, the sprint not
    passing within max_rounds, or the push failing) -- there is no partial
    "best effort" result.
    """
    config = load_config(working_dir)
    jira_config = _load_jira_config(config)

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
    await run_sprint(active_dir, feature_name, request, use_worktree=False)
    logger.info("issue_solver_sprint_finished", issue=issue["key"], branch=branch_name)

    _push_branch(active_dir, branch_name)
    logger.info("issue_solver_pushed", issue=issue["key"], branch=branch_name)

    return {"issue": issue["key"], "branch": branch_name}
