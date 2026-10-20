"""Consolidate completed runs, retaining the result and recoverable work."""

import re
import shutil
import tempfile
from pathlib import Path

from meow.execution.run_state import RunStore
from meow.infrastructure.logging import get_logger
from meow.infrastructure.worktree import _serialize_worktree_creation
from meow.infrastructure.worktree_controls import WorktreeSafetyError, _git, _registered
from meow.infrastructure.worktree_names import result_branch


def _checked(repo: Path, *args: str) -> str:
    result = _git(repo, *args)
    if result.returncode:
        raise WorktreeSafetyError(
            result.stderr.strip() or f"git {' '.join(args)} failed"
        )
    return result.stdout.strip()


def _integrated(repo: Path, commit: str, target: str) -> bool:
    if _git(repo, "merge-base", "--is-ancestor", commit, target).returncode == 0:
        return True
    # Task workers are cherry-picked, so ancestry alone cannot prove integration.
    cherry = _git(repo, "cherry", target, commit)
    return (
        cherry.returncode == 0
        and bool(cherry.stdout.strip())
        and all(line.startswith("- ") for line in cherry.stdout.splitlines())
    )


def _archive_runtime(repo: Path, path: Path) -> None:
    runtime = path / ".meow"
    if runtime.is_symlink() or runtime.is_junction():
        raise WorktreeSafetyError(f"{runtime} is a linked directory")
    archive_root = repo / ".meow" / "cleanup"
    archive_root.mkdir(parents=True, exist_ok=True)
    archive = Path(tempfile.mkdtemp(prefix=f"{path.name}.", dir=archive_root))
    shutil.copytree(runtime, archive / "runtime", symlinks=True)


@_serialize_worktree_creation
def remove_worker(  # ruff: ignore[too-many-arguments, too-many-statements, too-many-branches, complex-structure]
    repo: Path, path: Path, target: str, commit: str, *, branch: str | None = None
) -> bool:
    """Remove one verified worker without force; optionally delete its owned branch."""
    repo, path = repo.resolve(), path.resolve()
    if path.parent != (repo / ".worktrees").resolve():
        raise WorktreeSafetyError(f"{path} is outside the MEOW worktree root")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise WorktreeSafetyError("invalid task commit")
    registered = path in _registered(repo)
    if registered:
        head = _checked(path, "rev-parse", "HEAD")
        current = _checked(path, "branch", "--show-current")
        if head != commit or (current and current != branch):
            raise WorktreeSafetyError(f"{path} changed since its task completed")
        if _checked(path, "status", "--porcelain", "--untracked-files=all"):
            raise WorktreeSafetyError(f"{path} has uncommitted or untracked files")
        ignored = set(
            _checked(
                path, "ls-files", "--others", "--ignored", "--exclude-standard", "-z"
            ).split("\0")
        ) - {""}
        if any(not name.startswith(".meow/") for name in ignored):
            raise WorktreeSafetyError(f"{path} has ignored files")
    elif path.exists():
        raise WorktreeSafetyError(f"{path} is not a registered worktree")
    if not _integrated(repo, commit, target):
        raise WorktreeSafetyError(f"{path} has work not integrated into the result")
    if branch:
        ref = _git(repo, "show-ref", "--verify", "--hash", f"refs/heads/{branch}")
        if ref.returncode == 0 and ref.stdout.strip() != commit:
            raise WorktreeSafetyError(f"{branch} changed since its task completed")
    if registered:
        if ignored:
            _archive_runtime(repo, path)
        # Git refuses locked worktrees and submodules. Runtime evidence is archived.
        _checked(repo, "worktree", "remove", str(path))
    if (
        branch
        and _git(
            repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"
        ).returncode
        == 0
    ):
        # Explicit target ancestry above is stronger than branch -d's upstream check.
        _checked(repo, "branch", "-D", branch)
    return registered


def cleanup_run(  # ruff: ignore[too-many-statements, too-many-branches, complex-structure]
    repo: Path, run_id: str, *, integrated_into: str | None = None
) -> dict:
    store = RunStore(repo)
    record = store.load(run_id)
    report = {"run_id": run_id, "removed": [], "kept": []}
    if record.phase != "complete":
        report["kept"].append(f"run is {record.phase}")
        return report
    active = Path(record.worktree).resolve()
    if Path(record.repo).resolve() != repo.resolve() or (
        integrated_into is None and active not in _registered(repo)
    ):
        report["kept"].append(
            "result worktree is missing or belongs to another repository"
        )
        return report
    if (
        record.source != "orchestrator"
        and active.parent == (repo / ".worktrees").resolve()
    ):
        branch = result_branch(
            active,
            record.results.get("feature_name") or active.name,
            old=f"meow/{run_id}",
        )
        if record.branch != branch:
            store.transition(
                run_id,
                "complete",
                branch=branch,
                delivery={**record.delivery, "branch": branch},
            )
        report["branch"] = branch
    target = integrated_into or _checked(active, "rev-parse", "HEAD")
    for task_id, task in record.results.get("tasks", {}).items():
        path = Path(task.get("worktree") or repo / ".worktrees" / f"{run_id}-{task_id}")
        try:
            if path.resolve() == active or path.name != f"{run_id}-{task_id}":
                raise WorktreeSafetyError(f"{path} is not owned by this task")
            if task.get("status") != "complete" or not task.get("commit"):
                raise WorktreeSafetyError(f"task {task_id} is unfinished")
            if remove_worker(repo, path, target, task["commit"]):
                report["removed"].append(str(path.resolve()))
        except (OSError, RuntimeError) as exc:
            report["kept"].append(str(exc))
    store.update_results(run_id, cleanup=report)
    return report


def cleanup_completed(repo: Path, run_id: str) -> dict:
    """Best-effort post-completion cleanup never changes a successful verdict."""
    from meow.execution.orchestration.cleanup import consolidate_orchestrator
    from meow.execution.orchestration.model import is_orchestrator_id

    try:
        operation = (
            consolidate_orchestrator if is_orchestrator_id(run_id) else cleanup_run
        )
        report = operation(repo, run_id)
    except (OSError, RuntimeError, ValueError) as exc:
        report = {"run_id": run_id, "removed": [], "kept": [str(exc)]}
    for reason in report["kept"]:
        get_logger(__name__).warning("cleanup_kept", run_id=run_id, reason=reason)
    return report
