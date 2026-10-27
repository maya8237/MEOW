"""Launch, poll, resume, cancel, and gather evidence for child task runs."""

import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from meow.common.process import process_identity
from meow.execution.delivery import commit_worktree
from meow.execution.orchestration.model import (
    OrchestratorRecord,
    OrchestratorTask,
    task_branch,
    task_worktree_name,
)
from meow.execution.orchestration.scheduler import Launcher
from meow.execution.orchestration.store import OrchestratorStore
from meow.execution.run_failures import next_command
from meow.execution.run_state import (
    PARKED_PHASE,
    TERMINAL_PHASES,
    RunStateError,
    RunStore,
)
from meow.infrastructure.background import (
    BackgroundError,
    RunSeed,
    detach_command,
    inspect_worker,
    launch_background,
    reconcile_worker,
)
from meow.infrastructure.cancellation import request_cancel
from meow.infrastructure.supervisor import SUPERVISOR_ENV, _meow
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
        record = runs.load(run_id)
    except RunStateError:
        return "failed"
    phase = record.phase
    # A parked child whose supervisor still lives is relaunched within budget.
    supervised = (
        phase == PARKED_PHASE
        and inspect_worker(record, include_terminal=True).state == "running"
    )
    if phase in _PASSED_PHASES:
        return "passed"
    if not supervised and (
        phase in TERMINAL_PHASES or phase in {"interrupted_mutation", PARKED_PHASE}
    ):
        return "failed"  # settled without passing; triage decides what next
    return "running"


def journal_resume(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- one journal entry
    store: OrchestratorStore, orch_id: str, task_id: str, action: str, why: str
) -> None:
    """Journal a resume decision (`child_resumed`, `child_resume_skipped`,
    `child_resume_failed`) for the task's latest run."""

    def mutate(record: OrchestratorRecord) -> None:
        task = record.task(task_id)
        task.history.append({
            "at": datetime.now(UTC).isoformat(),
            "action": action,
            "cause": "",
            "rationale": why,
            "evidence": [],
            "run_id": task.run_ids[-1],
        })

    store.update(orch_id, mutate)


def resume_refusal(repo: Path, task: OrchestratorTask, run: dict) -> str | None:
    """Why the task's latest child run must go to triage instead of being
    resumed, or None to resume it. `run` is the `[run]` config table; the
    child budget is `managed_resumes` counted per task."""
    try:
        record = RunStore(repo).load(task.run_ids[-1])
    except RunStateError as exc:
        return f"run record unreadable: {exc}"
    park = record.park if isinstance(record.park, dict) else {}
    resumed = sum(1 for e in task.history if e.get("action") == "child_resumed")
    checks = (
        (record.phase != PARKED_PHASE, f"run is {record.phase}, not parked"),
        (park.get("kind") == "blocking", f"blocking failure: {park.get('cause')}"),
        (next_command(record) is None, f"{record.source} runs are not resumable"),
        (
            int(park.get("total_resumes", 0)) >= run["max_total_resumes"],
            "max_total_resumes reached",
        ),
        (resumed >= run["managed_resumes"], "managed_resumes reached"),
    )
    return next((reason for hit, reason in checks if hit), None)


def _spawn_resume(repo: Path, runs: RunStore, run_id: str) -> None:
    """Start a detached `meow resume RUN --continue` and register it as the
    run's worker, so polls see it running and `resume` accepts it."""
    nonce = secrets.token_hex(16)
    log = runs.directory / f"{run_id}.log"
    runs.update_background(run_id, nonce=nonce, log=str(log))
    env = {**os.environ, "PYTHONUNBUFFERED": "1", SUPERVISOR_ENV: nonce}
    argv = _meow("resume", run_id, "--continue", "--work-dir", str(repo))
    info = detach_command(repo, argv, str(log), env=env)
    pid = int(info["pid"])
    runs.update_background(
        run_id,
        pid=pid,
        start_identity=process_identity(pid),
        state="running",
        resumed_by="orchestrator",
    )


def resume_attempt(
    repo: Path, store: OrchestratorStore, orch_id: str, task_id: str
) -> str:
    """Relaunch the task's own parked child run through `meow resume RUN
    --continue` as a detached worker (same branch and worktree); return its
    run id, or "" when the worker could not start."""
    run_id = store.load(orch_id).task(task_id).run_ids[-1]
    try:
        _spawn_resume(repo, RunStore(repo), run_id)
    except (OSError, BackgroundError, RunStateError) as exc:
        why = str(exc) or type(exc).__name__
        journal_resume(store, orch_id, task_id, "child_resume_failed", why)
        return ""
    why = f"meow resume {run_id} --continue"
    journal_resume(store, orch_id, task_id, "child_resumed", why)
    return run_id


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
