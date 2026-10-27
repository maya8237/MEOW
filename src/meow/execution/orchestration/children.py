"""Launch, poll, cancel, and gather evidence for child task runs."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from meow.execution.delivery import commit_worktree
from meow.execution.orchestration.model import (
    OrchestratorRecord,
    OrchestratorTask,
    task_branch,
    task_worktree_name,
)
from meow.execution.orchestration.scheduler import Launcher
from meow.execution.orchestration.store import OrchestratorStore
from meow.execution.run_state import (
    PARKED_PHASE,
    TERMINAL_PHASES,
    RunStateError,
    RunStore,
)
from meow.infrastructure.background import RunSeed, launch_background, reconcile_worker
from meow.infrastructure.cancellation import request_cancel
from meow.infrastructure.worktree import _ensure_branch_worktree
from meow.project.plan_files import plan_test_file

_PASSED_PHASES = frozenset({"complete", "delivered"})


def _attempt_argv(
    orch_id: str,
    task: OrchestratorTask,
    *,
    test: bool,
    force_disable_tests: bool = False,
) -> list[str]:
    """The saved `_orchestrator-task` invocation for the task's next attempt."""
    resume = task.next_attempt or {}
    argv = [
        "_orchestrator-task",
        orch_id,
        task.id,
        "--resume-at",
        resume.get("resume_at", "generate"),
    ]
    if resume.get("plan_file"):
        argv += ["--plan", str(resume["plan_file"])]
    if force_disable_tests:
        argv.append("--force-disable-tests")
    if test and not force_disable_tests:
        argv.append("--test")
    if task.run_ids:
        argv.append("--preexisting")
    return argv


def _record_launch_failure(
    store: OrchestratorStore, orch_id: str, task_id: str, reason: str
) -> None:
    def mutate(record: OrchestratorRecord) -> None:
        task = record.task(task_id)
        task.status = "failed"
        task.history.append({
            "at": datetime.now(UTC).isoformat(),
            "action": "launch",
            "cause": "launch-failed",
            "rationale": reason,
            "evidence": [],
        })

    store.update(orch_id, mutate)


def _record_launch(
    store: OrchestratorStore,
    orch_id: str,
    task_id: str,
    launched: tuple[str, str, Path],
) -> None:
    run_id, branch, worktree = launched

    def mutate(record: OrchestratorRecord) -> None:
        task = record.task(task_id)
        task.run_ids.append(run_id)
        task.status = "running"
        task.branch = branch
        task.worktree = str(worktree)
        task.next_attempt = None

    store.update(orch_id, mutate)


def launch_attempt(  # ruff: ignore[too-many-arguments]
    repo: Path,
    store: OrchestratorStore,
    orch_id: str,
    task_id: str,
    *,
    config: dict,
    launcher: Launcher = launch_background,
) -> str:
    """Start one child run for a pending task; return its run id ("" on failure)."""
    record = store.load(orch_id)
    task = record.task(task_id)
    if task.status != "pending":
        raise ValueError(f"task {task_id} is {task.status}, not pending")
    if not record.integration.get("branch"):
        raise ValueError(
            "integration branch is not set; create the integration worktree first"
        )
    branch = task_branch(config["orchestrator"]["branch_prefix"], orch_id, task_id)
    try:
        worktree = _ensure_branch_worktree(
            repo,
            task_worktree_name(orch_id, task_id),
            branch,
            start_point=record.integration["branch"],
        )
        argv = _attempt_argv(
            orch_id,
            task,
            test=bool(record.options.get("test")),
            force_disable_tests=bool(record.options.get("force_disable_tests")),
        )
        run_id = launcher(
            repo, argv, seed=RunSeed("orchestrator", task.request, worktree, branch)
        )
    except Exception as exc:
        _record_launch_failure(store, orch_id, task_id, str(exc))
        return ""
    _record_launch(store, orch_id, task_id, (run_id, branch, worktree))
    return run_id


def poll_task(
    repo: Path, task: OrchestratorTask
) -> Literal["running", "passed", "failed"]:
    """Map the task's latest child run onto running, passed, or failed."""
    if not task.run_ids:
        raise ValueError(f"task {task.id} has no child run to poll")
    runs = RunStore(repo)
    run_id = task.run_ids[-1]
    try:
        reconcile_worker(runs, run_id)
        phase = runs.load(run_id).phase
    except RunStateError:
        return "failed"
    if phase in _PASSED_PHASES:
        return "passed"
    if phase in TERMINAL_PHASES or phase in {"interrupted_mutation", PARKED_PHASE}:
        return "failed"  # settled without passing; triage decides what next
    return "running"


def finalize_passed(repo: Path, task: OrchestratorTask) -> str:
    """Commit anything the child left uncommitted; return the task branch HEAD."""
    if not task.worktree:
        raise ValueError(f"task {task.id} has no worktree")
    return commit_worktree(Path(task.worktree), ["-m", f"Orchestrator task {task.id}"])


def _run_evidence(runs: RunStore, run_id: str) -> list[Path]:
    """The run journal plus whatever the run's record points at."""
    paths = [runs.directory / f"{run_id}.json"]
    try:
        record = runs.load(run_id)
    except RunStateError:
        return paths
    log = record.background.get("log")
    if isinstance(log, str) and log:
        paths.append(Path(log))
    if record.review_file:
        paths.append(Path(record.review_file))
    if record.plan_file:
        paths.append(plan_test_file(Path(record.plan_file)))
    return paths


def failure_evidence(
    repo: Path, task: OrchestratorTask, *, integration_log: Path | None = None
) -> list[str]:
    """Existing files that explain the task's latest failed attempt."""
    candidates = _run_evidence(RunStore(repo), task.run_ids[-1]) if task.run_ids else []
    if integration_log is not None:
        candidates.append(integration_log)
    return list(dict.fromkeys(str(path) for path in candidates if path.is_file()))


def cancel_children(repo: Path, record: OrchestratorRecord) -> list[str]:
    """Request cancellation of every running task's latest child run."""
    runs = RunStore(repo)
    signalled = []
    for task in record.tasks:
        if task.status != "running" or not task.run_ids:
            continue
        run_id = task.run_ids[-1]
        try:
            request_cancel(runs, run_id)
        except ValueError:
            continue
        signalled.append(run_id)
    return signalled
