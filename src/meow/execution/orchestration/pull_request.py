"""Open the pull/merge request for a pushed integration branch.

Provider problems never fail an orchestrator: the pushed branch is the
deliverable, so a missing provider or a failed call is recorded as
`delivery.pr_error` (with a manual link) and the orchestrator still ends
`delivered`.
"""

import re
import subprocess
from pathlib import Path
from urllib.parse import quote, urlparse

from meow.agents.base import ProjectContext
from meow.execution.orchestration.model import OrchestratorRecord
from meow.execution.orchestration.report import render_report
from meow.execution.orchestration.store import OrchestratorStore
from meow.integrations.github_reviewer import _load_github_config
from meow.integrations.gitlab_reviewer import _load_gitlab_config

_TITLE_LIMIT = 200
_MIN_HOST_LENGTH = 2  # a one-letter "host" is a Windows drive, not a remote
_SCP_REMOTE = re.compile(r"^(?:[^@/\s]+@)?(?P<host>[^:/\s]+):(?!//)(?P<path>\S+)$")
_PROVIDER_LOADERS = {"github": _load_github_config, "gitlab": _load_gitlab_config}


def parse_remote(remote_url: str) -> tuple[str, str] | None:
    """(host, repository path) of a network remote; None for a local path."""
    url = remote_url.strip()
    if "://" in url:
        parsed = urlparse(url)
        host, path = parsed.hostname, parsed.path
    elif match := _SCP_REMOTE.match(url):
        host, path = match["host"], match["path"]
    else:
        return None
    if not host or len(host) < _MIN_HOST_LENGTH:
        return None
    path = path.strip("/").removesuffix(".git")
    return (host.lower(), path) if path else None


def _table(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _gitlab_host(config: dict) -> str | None:
    env = _table(_table(_table(config.get("gitlab")).get("mcp")).get("env"))
    url = env.get("GITLAB_URL")
    if not isinstance(url, str) or not url:
        return None
    host = urlparse(url if "://" in url else f"//{url}").hostname
    return host.lower() if host else None


def detect_provider(remote_url: str, config: dict) -> str | None:
    """`github` or `gitlab` from the origin host (see `parse_remote`); None when
    the host matches neither. Whether the provider is configured is checked
    when the pull request is opened."""
    parsed = parse_remote(remote_url)
    if parsed is None:
        return None
    host = parsed[0]
    if "github" in host:
        return "github"
    if "gitlab" in host or host == _gitlab_host(config):
        return "gitlab"
    return None


def manual_pr_link(
    remote_url: str, provider: str | None, base: str, branch: str
) -> str | None:
    """Where a person can open the PR/MR by hand; None without a known provider."""
    parsed = parse_remote(remote_url)
    if parsed is None or provider not in _PROVIDER_LOADERS:
        return None
    host, path = parsed
    source, target = quote(branch, safe="/"), quote(base, safe="/")
    if provider == "github":
        return f"https://{host}/{path}/compare/{target}...{source}?expand=1"
    return (
        f"https://{host}/{path}/-/merge_requests/new"
        f"?merge_request[source_branch]={source}&merge_request[target_branch]={target}"
    )


def pull_request_title(record: OrchestratorRecord) -> str:
    if record.source in {"jira", "jql"}:
        keys = [task.source_ref for task in record.tasks if task.source_ref]
        keys = keys or list(record.intake.get("jira_keys", ()))
        if keys:
            return "Orchestrator: " + ", ".join(keys)
    lines = (line.strip() for line in record.objective.splitlines())
    first = next((line for line in lines if line), record.id)
    return f"Orchestrator: {first[:_TITLE_LIMIT]}"


def origin_url(repo: Path) -> str | None:
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def provider_mcp(config: dict, provider: str) -> dict:
    """The provider's `[<provider>.mcp]` table, or a one-line reason it is missing."""
    try:
        return _PROVIDER_LOADERS[provider](config)["mcp"]
    except ValueError as exc:
        reason = next(iter(str(exc).splitlines()), "invalid configuration")
        raise RuntimeError(f"provider not configured: {reason}") from exc


class _Opening:
    def __init__(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- one value per collaborator
        self,
        repo: Path,
        store: OrchestratorStore,
        orch_id: str,
        config: dict,
        agents,
    ):
        self.repo = repo
        self.store = store
        self.orch_id = orch_id
        self.config = config
        self.agents = agents
        self.record = store.load(orch_id)
        self.remote = origin_url(repo)
        self.provider = detect_provider(self.remote, config) if self.remote else None
        self.branch = self.record.integration["branch"]

    def link(self) -> str | None:
        if not self.remote:
            return None
        return manual_pr_link(
            self.remote, self.provider, self.record.base_branch, self.branch
        )

    async def create(self) -> dict:
        if not self.remote:
            raise RuntimeError("no origin remote is configured")
        if self.provider is None:
            raise RuntimeError(
                f"no GitHub or GitLab integration matches the origin remote "
                f"{self.remote}"
            )
        mcp = provider_mcp(self.config, self.provider)
        context = ProjectContext(
            Path(self.record.integration["worktree"]),
            self.config,
            use_worktree=True,
        )
        agent = self.agents.pull_request(context, mcp, self.provider)
        url = await agent.open(
            pull_request_title(self.record),
            render_report(self.repo, self.record),
            self.branch,
            self.record.base_branch,
            self.store.dir_for(self.orch_id) / "pull-request.json",
        )
        return {"url": url, "provider": self.provider}

    def save(self, pr: dict | None, error: str | None) -> None:
        link = None if pr else self.link()

        def mutate(record: OrchestratorRecord) -> None:
            record.delivery["pr"] = pr
            record.delivery["pr_error"] = error
            record.delivery["pr_manual_link"] = link

        self.store.update(self.orch_id, mutate)


async def open_pull_request(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- the pinned delivery signature
    repo: Path, store: OrchestratorStore, orch_id: str, config: dict, agents
) -> None:
    """Open the PR/MR once; record the URL, or why none was opened."""
    opening = _Opening(repo, store, orch_id, config, agents)
    if opening.record.delivery.get("pr"):
        return
    try:
        pr = await opening.create()
    except Exception as exc:  # a provider problem is reported, never raised
        opening.save(None, str(exc) or type(exc).__name__)
    else:
        opening.save(pr, None)
