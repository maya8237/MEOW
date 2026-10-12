"""Isolated task workers and incremental integration into one checkout."""

import asyncio
import re
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from .integration import integrate_results
from .model import TaskGraph, TaskSpec
from .scheduler import TaskOutcome, run_task_graph

_RUN_ID = re.compile(r"[0-9a-f]{32}\Z")


async def _git(repo: Path, *args: str) -> str:
    done = await asyncio.to_thread(
        subprocess.run,
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )
    return done.stdout.strip()


def _discard_worktree(repo: Path, task_dir: Path) -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(task_dir)],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if task_dir.exists():
        shutil.rmtree(task_dir, ignore_errors=True)
    subprocess.run(
        ["git", "worktree", "prune"], cwd=repo, capture_output=True, check=False
    )


def _owned(path: str, spec: TaskSpec) -> bool:
    value = path.replace("\\", "/").casefold()
    return any(
        value == owner.casefold() or value.startswith(f"{owner.casefold()}/")
        for owner in spec.owned_paths
    )


def _ensure_ignored(root: Path, common_dir: Path) -> None:
    """Make `.worktrees/` ignored, locally, instead of refusing to run."""
    probe = subprocess.run(
        ["git", "check-ignore", "-q", ".worktrees/task-probe"], cwd=root, check=False
    )
    if probe.returncode == 0:
        return
    exclude = common_dir / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if ".worktrees/" not in existing.splitlines():
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        exclude.write_text(f"{existing}{prefix}.worktrees/\n", encoding="utf-8")


def preflight(repo: Path) -> str | None:
    """Why parallel task execution cannot start here, or None when it can."""
    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None  # the executor reports git problems itself
    if dirty.returncode == 0 and dirty.stdout.strip():
        return (
            "integration checkout has uncommitted changes; commit or stash "
            "them so task commits can be integrated"
        )
    return None


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
    on_outcome: Callable[[TaskOutcome], None] | None = None,
    completed: frozenset[str] | set[str] = frozenset(),
) -> TaskExecutionResult:
    """Run each slice in its own Git worktree and integrate after completion."""
    graph.validate()
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run ID")
    repo = repo.resolve()
    common_dir = Path(
        await _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    )
    root = common_dir.parent.resolve()
    worker_root = root / ".worktrees"
    worker_root.mkdir(exist_ok=True)
    await asyncio.to_thread(_ensure_ignored, root, common_dir)
    head = await _git(repo, "rev-parse", "HEAD")
    if await _git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("integration checkout must be clean")
    conflict = None
    add_lock = asyncio.Lock()

    async def worker(spec: TaskSpec) -> TaskOutcome:
        task_dir = worker_root / f"{run_id}-{spec.id}"
        # A failure leaves the checkout for inspection; a later attempt (a
        # retry or `meow resume`) discards that stale copy and starts clean.
        async with add_lock:
            if task_dir.exists() or str(task_dir) in await _git(
                repo, "worktree", "list", "--porcelain"
            ):
                await asyncio.to_thread(_discard_worktree, repo, task_dir)
            await _git(repo, "worktree", "add", "--detach", str(task_dir), head)
        if prepare is not None:
            await prepare(task_dir)
        await implement(spec, task_dir)
        await _git(task_dir, "add", "-A")
        staged = (await _git(task_dir, "diff", "--cached", "--name-only")).splitlines()
        if not staged:
            raise ValueError(f"task {spec.id} produced no changes")
        outside = [path for path in staged if not _owned(path, spec)]
        if outside:
            raise ValueError(f"task {spec.id} edited outside ownership: {outside}")
        await _git(
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
            spec.id,
            "complete",
            str(task_dir),
            await _git(task_dir, "rev-parse", "HEAD"),
        )

    async def integrate(outcome: TaskOutcome) -> bool:
        nonlocal head, conflict
        result = await asyncio.to_thread(integrate_results, repo, head, (outcome,))
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
        on_outcome=on_outcome,
        completed=completed,
    )
    return TaskExecutionResult(outcomes, head, conflict, len(graph.tasks))
