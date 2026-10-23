"""Final delivery of a plain run: open the merge/pull request for its branch."""

import re
from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.orchestration import OrchestratorAgents
from meow.execution.orchestration.pull_request import (
    detect_provider,
    manual_pr_link,
    origin_url,
    provider_mcp,
)
from meow.execution.run_state import RunStore
from meow.infrastructure.cancellation import RunCancelled

_TITLE_LIMIT = 72
_SECRET = re.compile(
    r"(\b\w*(?:TOKEN|KEY|SECRET|PASSWORD)\w*\s*[=:]\s*)\S+", re.IGNORECASE
)


def _one_line(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return _SECRET.sub(r"\1***", text) or type(exc).__name__


def _body(record) -> str:
    lines = [
        record.request.strip(),
        "",
        f"MEOW run: `{record.id}`",
        "",
        "Local review and checks passed before delivery.",
    ]
    if record.plan_file:
        lines += ["", f"Plan: `{record.plan_file}`"]
    return "\n".join(lines)


async def _open(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- one value per collaborator
    store: RunStore, record, config: dict, agents, remote: str | None, provider
) -> str:
    base = record.delivery.get("base")
    if not base:
        raise RuntimeError("no base branch recorded")
    if not remote:
        raise RuntimeError("no origin remote is configured")
    if provider is None:
        raise RuntimeError(
            f"no GitHub or GitLab integration matches the origin remote {remote}"
        )
    mcp = provider_mcp(config, provider)
    context = ProjectContext(Path(record.worktree), config, use_worktree=True)
    lines = (line.strip() for line in record.request.splitlines())
    first = next((line for line in lines if line), record.id)
    return await agents.pull_request(context, mcp, provider).open(
        f"MEOW: {first[:_TITLE_LIMIT]}",
        _body(record),
        record.branch,
        base,
        store.directory / f"{record.id}.pull-request.json",
    )


async def open_run_merge_request(
    store: RunStore, run_id: str, config: dict, agents: OrchestratorAgents
) -> dict | None:
    """Open the MR/PR for the run branch into `delivery["base"]`.

    Returns `{"url", "provider"}`, the saved value when already opened, or None
    after recording why it could not be opened. Provider problems never fail a
    run; cancellation still propagates.
    """
    record = store.load(run_id)
    if record.delivery.get("pr"):
        return record.delivery["pr"]
    remote = origin_url(Path(record.repo))
    provider = detect_provider(remote, config) if remote else None
    pr = error = link = None
    try:
        url = await _open(store, record, config, agents, remote, provider)
        pr = {"url": url, "provider": provider}
    except RunCancelled:
        raise
    except Exception as exc:  # provider problems must not fail a delivered run
        error = _one_line(exc)
        base = record.delivery.get("base")
        if remote and base:
            link = manual_pr_link(remote, provider, base, record.branch)
    delivery = {**record.delivery, "pr": pr, "pr_error": error, "pr_manual_link": link}
    store.transition(run_id, record.phase, delivery=delivery)
    return pr
