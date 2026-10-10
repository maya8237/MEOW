"""Isolated task workers and incremental integration into one checkout."""

import re
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from .integration import integrate_results
from .model import TaskGraph, TaskSpec
from .scheduler import TaskOutcome, run_task_graph

_RUN_ID = re.compile(r"[0-9a-f]{32}\Z")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True
    ).stdout.strip()


def _owned(path: str, spec: TaskSpec) -> bool:
    value = path.replace("\\", "/").casefold()
    return any(
        value == owner.casefold() or value.startswith(f"{owner.casefold()}/")
        for owner in spec.owned_paths
    )


@dataclass(frozen=True)
class TaskExecutionResult:
    outcomes: tuple[TaskOutcome, ...]
    revision: str
    conflict: str | None = None
    expected_tasks: int = 0

    @property
    def passed(self) -> bool:
        return (
            self.conflict is None
            and len(self.outcomes) == self.expected_tasks
            and all(outcome.status == "complete" for outcome in self.outcomes)
        )


async def execute_in_worktrees(  # ruff: ignore[complex-structure, too-many-arguments, too-many-statements]
    repo: Path,
    graph: TaskGraph,
    run_id: str,
    implement: Callable[[TaskSpec, Path], Awaitable[None]],
    *,
    max_parallel: int,
    cancel: Callable[[], bool] = lambda: False,
    on_change: Callable[[str, str], None] | None = None,
    prepare: Callable[[Path], Awaitable[None]] | None = None,
) -> TaskExecutionResult:
    """Run each slice in its own Git worktree and integrate after completion."""
    graph.validate()
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run ID")
    repo = repo.resolve()
    common_dir = Path(
        _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    )
    root = common_dir.parent.resolve()
    worker_root = root / ".worktrees"
    worker_root.mkdir(exist_ok=True)
    if subprocess.run(
        ["git", "check-ignore", "-q", ".worktrees/task-probe"],
        cwd=root,
        check=False,
    ).returncode:
        raise ValueError(".worktrees must be gitignored before task execution")
    head = _git(repo, "rev-parse", "HEAD")
    if _git(repo, "status", "--porcelain"):
        raise ValueError("integration checkout must be clean")
    conflict = None

    async def worker(spec: TaskSpec) -> TaskOutcome:
        task_dir = worker_root / f"{run_id}-{spec.id}"
        if task_dir.exists():
            raise ValueError(f"task worktree already exists: {task_dir}")
        # A failure leaves the registered checkout and its edits for inspection.
        _git(repo, "worktree", "add", "--detach", str(task_dir), head)
        if prepare is not None:
            await prepare(task_dir)
        await implement(spec, task_dir)
        _git(task_dir, "add", "-A")
        staged = _git(task_dir, "diff", "--cached", "--name-only").splitlines()
        if not staged:
            raise ValueError(f"task {spec.id} produced no changes")
        outside = [path for path in staged if not _owned(path, spec)]
        if outside:
            raise ValueError(f"task {spec.id} edited outside ownership: {outside}")
        _git(
            task_dir,
            "-c",
            "user.name=MEOW",
            "-c",
            "user.email=meow@local",
            "commit",
            "-m",
            f"meow task {spec.id}",
        )
        return TaskOutcome(
            spec.id, "complete", str(task_dir), _git(task_dir, "rev-parse", "HEAD")
        )

    async def integrate(outcome: TaskOutcome) -> bool:  # ruff: ignore[unused-async]
        nonlocal head, conflict
        result = integrate_results(repo, head, (outcome,))
        if result.conflict:
            conflict = result.conflict
            return False
        head = result.revision
        return True

    outcomes = await run_task_graph(
        graph,
        worker,
        max_parallel=max_parallel,
        cancel=cancel,
        on_change=on_change,
        on_complete=integrate,
    )
    return TaskExecutionResult(outcomes, head, conflict, len(graph.tasks))
