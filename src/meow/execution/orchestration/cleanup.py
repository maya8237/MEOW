"""`meow worktree clean ORCHESTRATOR_ID`: remove a delivered orchestrator's
worktrees, never its branches and never anything that could hold unsaved work."""

from pathlib import Path

from meow.execution.cleanup import cleanup_run, remove_worker
from meow.execution.orchestration.model import OrchestratorRecord
from meow.execution.orchestration.store import OrchestratorStore
from meow.execution.run_state import RunStore
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
    reasons = []
    for task in record.tasks:
        if not task.branch:
            continue
        ref = _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{task.branch}")
        if ref.returncode and not (task.worktree and Path(task.worktree).exists()):
            continue  # Already removed by successful consolidation.
        if _git(
            repo, "merge-base", "--is-ancestor", task.branch, integration
        ).returncode:
            reasons.append(
                f"task branch {task.branch} is not merged into {integration}"
            )
    return reasons


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


def consolidate_orchestrator(repo: Path, orch_id: str) -> dict:  # ruff: ignore[too-many-statements, too-many-branches, complex-structure]
    """Keep the delivered integration checkout and discard merged task artifacts."""
    repo = repo.resolve()
    store = OrchestratorStore(repo)
    record = store.load(orch_id)
    report = {"run_id": orch_id, "removed": [], "kept": []}
    if record.phase != "delivered":
        report["kept"].append(f"orchestrator is {record.phase}, not delivered")
        return report
    target = record.integration.get("branch")
    active = Path(record.integration.get("worktree") or repo).resolve()
    if not target or active not in _registered(repo):
        report["kept"].append("integration worktree is missing")
        return report
    for task in record.tasks:
        if not task.branch or not task.worktree:
            continue
        path = Path(task.worktree).resolve()
        try:
            if (
                task.status != "merged"
                or task.branch != f"{target}-{task.id}"
                or path == active
            ):
                raise WorktreeSafetyError(
                    f"task {task.id} is unfinished or not owned by this orchestrator"
                )
            for child_id in task.run_ids:
                child = RunStore(repo).load(child_id)
                if (
                    child.source != "orchestrator"
                    or Path(child.worktree).resolve() != path
                ):
                    raise WorktreeSafetyError(
                        f"run {child_id} is not owned by task {task.id}"
                    )
                nested = cleanup_run(repo, child_id, integrated_into=target)
                report["removed"].extend(nested["removed"])
                report["kept"].extend(nested["kept"])
            ref = _git(repo, "rev-parse", "--verify", f"refs/heads/{task.branch}")
            if ref.returncode:
                if path in _registered(repo) or path.exists():
                    raise WorktreeSafetyError(f"task {task.id} branch is missing")
                continue
            commit = ref.stdout.strip()
            if _git(repo, "merge-base", "--is-ancestor", commit, target).returncode:
                raise WorktreeSafetyError(f"task {task.id} has unmerged commits")
            if remove_worker(repo, path, target, commit, branch=task.branch):
                report["removed"].append(str(path))
        except (OSError, RuntimeError, ValueError) as exc:
            report["kept"].append(str(exc))
    store.update(orch_id, lambda rec: rec.delivery.update(cleanup=report))
    return report
