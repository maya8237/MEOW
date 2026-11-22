"""The `meow run --jira/--gitlab/--github` flow: fetch a Jira issue (a given
key, or the most recently created one in a configured project) or the
GitLab/GitHub issue, MR or PR at a URL, then solve it through the
same generator<->reviewer loop plain `meow run` uses (`sprint_runner.run_sprint`),
inside a dedicated, named-branch worktree. A verified run commits and pushes
the branch to origin.

Unlike `run`/`plan`'s initially detached worktrees, this flow starts on a
named branch so `worktree.py`
creates its own worktree on one instead of the usual detached one.
"""

from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from meow.agents.base import ProjectContext
from meow.agents.mcp_fetcher import (
    GithubFetcherAgent,
    GitlabFetcherAgent,
    IssueFetcherAgent,
    fetch_record,
)
from meow.execution.run_state import RunStore
from meow.execution.sprint_runner import run_sprint
from meow.infrastructure.cancellation import cancellable
from meow.infrastructure.logging import get_logger
from meow.infrastructure.worktree import _ensure_branch_worktree, sanitize_name
from meow.integrations.github_reviewer import _load_github_config
from meow.integrations.gitlab_reviewer import _load_gitlab_config
from meow.project.config import load_config
from meow.project.prompts import with_instructions

logger = get_logger(__name__)

DEFAULT_BRANCH_PREFIX = "issue/"


class IssueUnresolvedError(RuntimeError):
    """`meow run --jira` could not fetch or solve the issue."""


def _load_jira_config(config: dict) -> dict:
    """Resolve this provider only when requested."""
    from meow.project.mcp_registry import provider_mcp_config

    mcp = provider_mcp_config(config, "jira")
    jira = config.get("jira") if isinstance(config.get("jira"), dict) else {}
    return {
        "project_key": jira.get("project_key"),
        "branch_prefix": jira.get("branch_prefix", DEFAULT_BRANCH_PREFIX),
        "mcp": mcp,
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


PROVIDER_LABELS = {"jira": "Jira", "gitlab": "GitLab", "github": "GitHub"}
_LINK_PROVIDERS = {
    "gitlab": (GitlabFetcherAgent, _load_gitlab_config),
    "github": (GithubFetcherAgent, _load_github_config),
}


def link_slug(link: str) -> str:
    """A short, stable name for a GitLab/GitHub URL: `repo-issues-12`."""
    parts = [part for part in urlparse(link).path.split("/") if part and part != "-"]
    return sanitize_name("-".join(parts[-3:]) or "item").lower()


async def _fetch_link(
    working_dir: Path, config: dict, provider: str, link: str
) -> dict:
    """The issue, MR or PR at `link`, as `{"title", "description"}`."""
    fetcher_type, load = _LINK_PROVIDERS[provider]
    fetcher = fetcher_type(ProjectContext(working_dir, config), load(config)["mcp"])
    return await fetch_record(
        fetcher,
        lambda output_file: fetcher.fetch_item(link, output_file),
        ("title", "description"),
    )


async def run_issue_solver(  # ruff: ignore[too-many-arguments]
    working_dir: Path,
    issue_key: str | None = None,
    *,
    provider: str = "jira",
    approve_plan: Callable[[Path], bool] | None = None,
    test: bool = False,
    force_disable_tests: bool = False,
    run_id: str | None = None,
    workflow: str | None = None,
    inputs: dict[str, str] | None = None,
    instructions: str | None = None,
) -> dict:
    """Fetch a Jira issue (or, with `provider` gitlab/github, the issue, MR or
    PR whose URL is `issue_key`), solve it in a worktree, and return its branch.

    `instructions` (the run's request text) are appended to the fetched item.

    Returns `{"issue": <key or URL>, "branch": <branch name>}` on success.
    Raises on any failure (no active MCP, no matching item, the plan being
    declined when `approve_plan` is given, or the sprint not passing within
    max_rounds) -- there is no partial "best effort"
    result. `approve_plan` is passed straight through to `run_sprint`; see
    its docstring.
    """
    config = load_config(working_dir)
    jira_config = _load_jira_config(config) if provider == "jira" else None

    try:
        return await _solve_issue(
            working_dir,
            config,
            jira_config,
            issue_key,
            approve_plan,
            run_id=run_id,
            provider=provider,
            **({"test": True} if test else {}),
            **({"force_disable_tests": True} if force_disable_tests else {}),
            **({"workflow": workflow} if workflow else {}),
            **({"inputs": inputs} if inputs else {}),
            **({"instructions": instructions} if instructions else {}),
        )
    except Exception as exc:
        logger.warning("issue_unresolved", issue=issue_key or "latest", reason=str(exc))
        raise IssueUnresolvedError(str(exc)) from exc


async def _fetch_work(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- one value per source detail
    working_dir: Path,
    config: dict,
    jira_config: dict | None,
    provider: str,
    ref: str | None,
) -> dict:
    """`{"key", "request", "feature", "branch"}` for the fetched item."""
    if provider == "jira":
        issue = await _fetch_issue(working_dir, config, jira_config, ref)
        return {
            "key": issue["key"],
            "request": (
                f"Resolve Jira issue {issue['key']}: {issue['summary']}\n\n"
                f"{issue['description']}"
            ),
            "feature": f"issue-{sanitize_name(issue['key'])}".lower(),
            "branch": f"{jira_config['branch_prefix']}{issue['key']}",
        }
    item = await _fetch_link(working_dir, config, provider, str(ref))
    slug = link_slug(str(ref))
    return {
        "key": ref,
        "request": (
            f"Resolve the {PROVIDER_LABELS[provider]} item {ref}: {item['title']}"
            f"\n\n{item['description']}"
        ),
        "feature": f"{provider}-{slug}",
        "branch": f"{DEFAULT_BRANCH_PREFIX}{provider}-{slug}",
    }


async def _solve_issue(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- reducing args would change run_issue_solver's call site
    working_dir: Path,
    config: dict,
    jira_config: dict | None,
    issue_key: str | None,
    approve_plan: Callable[[Path], bool] | None,
    test: bool = False,
    force_disable_tests: bool = False,
    run_id: str | None = None,
    workflow: str | None = None,
    inputs: dict[str, str] | None = None,
    instructions: str | None = None,
    provider: str = "jira",
) -> dict:
    fetch = _fetch_work(working_dir, config, jira_config, provider, issue_key)
    work = (
        await cancellable(RunStore(working_dir), run_id, fetch)
        if run_id
        else await fetch
    )
    feature_name, branch_name = work["feature"], work["branch"]

    existing_worktree = (working_dir / ".worktrees" / feature_name).exists()
    active_dir = _ensure_branch_worktree(working_dir, feature_name, branch_name)
    request = with_instructions(work["request"], instructions)
    if run_id:
        RunStore(working_dir).transition(
            run_id,
            "issue_fetched",
            request=request,
            worktree=str(active_dir),
            branch=branch_name,
        )

    logger.info("issue_solver_sprint_started", issue=work["key"], branch=branch_name)
    run_options = {"run_id": run_id} if run_id else {}
    if existing_worktree:
        run_options["worktree_preexisting"] = True
    await run_sprint(
        active_dir,
        feature_name,
        request,
        use_worktree=False,
        approve_plan=approve_plan,
        record_root=working_dir,
        source=provider,
        **run_options,
        **({"test": True} if test else {}),
        **({"force_disable_tests": True} if force_disable_tests else {}),
        **({"workflow": workflow} if workflow else {}),
        **({"inputs": inputs} if inputs else {}),
    )
    logger.info("issue_solver_sprint_finished", issue=work["key"], branch=branch_name)

    return {"issue": work["key"], "branch": branch_name}
