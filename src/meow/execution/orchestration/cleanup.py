"""`meow worktree clean ORCHESTRATOR_ID`: remove a delivered orchestrator's
worktrees, never its branches and never anything that could hold unsaved work."""

from pathlib import Path

from meow.execution.orchestration.model import OrchestratorRecord
from meow.execution.orchestration.store import OrchestratorStore
from meow.infrastructure.worktree_controls import (
    WorktreeSafetyError,
    _git,
    _registered,
)


def _worktree_paths(repo: Path, record: OrchestratorRecord) -> list[Path]:
    """Task worktrees first, then the integration worktree."""
    names = [task.worktree for task in record.tasks if task.worktree]
    names.append(record.integration.get("worktree") or "")
    paths = []
    for name in names:
        if name:
            path = Path(name)
            paths.append((path if path.is_absolute() else repo / path).resolve())
    return paths


def _is_dirty(path: Path) -> bool:
    status = _git(path, "status", "--porcelain", "--untracked-files=all")
    return status.returncode != 0 or bool(status.stdout.strip())


def _location_refusals(repo: Path, paths: list[Path]) -> list[str]:
    root = (repo / ".worktrees").resolve()
    registered = _registered(repo)
    reasons = []
    for path in paths:
        if path.parent != root:
            reasons.append(f"{path} is outside the MEOW worktree root")
        elif path in registered and _is_dirty(path):
            reasons.append(f"{path} has uncommitted or untracked files")
    return reasons


def _branch_refusals(repo: Path, record: OrchestratorRecord) -> list[str]:
    integration = record.integration.get("branch")
    if not integration:
        return ["the orchestrator has no integration branch"]
    return [
        f"task branch {task.branch} is not merged into {integration}"
        for task in record.tasks
        if task.branch
        and _git(
            repo, "merge-base", "--is-ancestor", task.branch, integration
        ).returncode
    ]


def _push_refusals(repo: Path, record: OrchestratorRecord) -> list[str]:
    branch = record.integration.get("branch")
    pushed = record.delivery.get("pushed_commit")
    if not pushed:
        return [f"{branch} was never pushed"]
    remote = _git(repo, "ls-remote", "origin", f"refs/heads/{branch}")
    fields = remote.stdout.split() if remote.returncode == 0 else []
    if not fields or fields[0] != pushed:
        return [f"origin does not hold {branch} at the delivered commit {pushed[:12]}"]
    return []


def _remove(repo: Path, paths: list[Path]) -> list[Path]:
    registered = _registered(repo)
    removed = []
    for path in paths:
        if path not in registered or not path.is_dir():
            continue
        result = _git(repo, "worktree", "remove", str(path))
        if result.returncode:
            raise WorktreeSafetyError(
                result.stderr.strip() or "git worktree remove failed"
            )
        removed.append(path)
    _git(repo, "worktree", "prune")
    return removed


def clean_orchestrator(repo: Path, orch_id: str) -> list[Path]:
    """Remove the task worktrees, then the integration worktree.

    Requires a delivered orchestrator whose worktrees are clean, whose task
    branches are all merged into the integration branch, and whose pushed
    commit is what `origin` holds. Branches are kept. Returns the removed paths.
    """
    repo = repo.resolve()
    record = OrchestratorStore(repo).load(orch_id)
    if record.phase != "delivered":
        raise WorktreeSafetyError(
            f"Refusing cleanup: orchestrator {orch_id} is {record.phase}, not delivered"
        )
    paths = _worktree_paths(repo, record)
    reasons = [
        *_location_refusals(repo, paths),
        *_branch_refusals(repo, record),
        *_push_refusals(repo, record),
    ]
    if reasons:
        raise WorktreeSafetyError("Refusing cleanup: " + "; ".join(reasons))
    return _remove(repo, paths)
