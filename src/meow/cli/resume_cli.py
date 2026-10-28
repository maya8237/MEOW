"""Inspect and explicitly resume a saved run after identity validation."""

import hashlib
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout, suppress
from pathlib import Path

from meow.cli.orchestration_cli import resume_orchestration
from meow.cli.status_cli import (
    RESUMABLE_SOURCES,
    render,
    saved_run_id,
)
from meow.execution.orchestration.model import is_orchestrator_id
from meow.execution.orchestration.report import render_status
from meow.execution.orchestration.store import OrchestratorStateError, OrchestratorStore
from meow.execution.queue_state import QueueStateError, QueueStore, QueueWorkerLock
from meow.execution.run_failures import never_prepared, next_command
from meow.execution.run_state import (
    PARKED_PHASE,
    RunRecord,
    RunStateError,
    RunStore,
    driver_alive,
)
from meow.execution.sprint_runner import run_sprint
from meow.infrastructure.background import inspect_worker, redrive_saved_invocation
from meow.infrastructure.cancellation import clear_cancel
from meow.infrastructure.checks import config_fingerprint
from meow.infrastructure.supervisor import (
    EXIT_BLOCKED,
    EXIT_TEMPFAIL,
    SUPERVISOR_ENV,
)
from meow.infrastructure.worktree import current_branch
from meow.project.config import config_root, load_config, run_config
from meow.project.custom.workflows import expand, fingerprint, select_workflow, walk


def _config_dir(record: RunRecord) -> Path:
    """Where this run's config lives: the repo, or the worktree that was
    auto-onboarded and has not merged its `.meow/config.toml` yet."""
    return config_root(Path(record.repo), Path(record.worktree))


def _validate(  # ruff: ignore[complex-structure, too-many-return-statements, too-many-branches, too-many-statements]
    record: RunRecord, working_dir: Path
) -> str | None:
    repo = Path(record.repo).resolve()
    worktree = Path(record.worktree).resolve()
    if repo != working_dir.resolve():
        return f'Repository mismatch. Use --work-dir "{record.repo}".'
    if not worktree.is_dir():
        return f"Worktree missing: {worktree}. Restore it before continuing."
    common_dirs = []
    for directory in (repo, worktree):
        try:
            identity = subprocess.run(
                ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return "Git unavailable. Restore Git before continuing."
        if identity.returncode:
            return f"Worktree identity unavailable: {directory}. Restore it."
        common_dirs.append(Path(identity.stdout.strip()).resolve())
    if common_dirs[0] != common_dirs[1]:
        return "Worktree belongs to another repository. Restore the saved worktree."
    branch = current_branch(worktree)
    if branch != record.branch:
        return (
            f"Branch mismatch: saved {record.branch}, found {branch}. "
            "Restore the saved branch."
        )
    if record.plan_file:
        plan = Path(record.plan_file)
        if not plan.is_file():
            return f"Plan missing: {plan}. Restore the saved plan before continuing."
        if (
            record.plan_fingerprint
            and hashlib.sha256(plan.read_bytes()).hexdigest() != record.plan_fingerprint
        ):
            return f"Plan changed: {plan}. Restore the saved plan before continuing."
    if record.config_fingerprint:
        try:
            current = config_fingerprint(_config_dir(record))
        except OSError:
            return (
                "Configuration missing. Restore the repository MEOW config "
                "before continuing."
            )
        if current != record.config_fingerprint:
            return (
                "Configuration changed. Restore the repository MEOW config "
                "before continuing."
            )
    if saved := record.results.get("workflow"):
        try:
            select_workflow(load_config(_config_dir(record)), saved=saved)
        except (OSError, ValueError, KeyError) as exc:
            return str(exc)
    return None


def _repair(  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
    record: RunRecord, working_dir: Path
) -> tuple[dict, list[str]]:
    """Adopt the current on-disk state for every resume gate that can drift.

    Returns the record fields to rewrite and a human-readable list of actions.
    Only identity drift is repaired; a repository from another checkout or a
    worktree that cannot be located stays a hard error.
    """
    patch: dict = {}
    actions: list[str] = []
    repo = Path(record.repo)
    worktree = Path(record.worktree)
    if not worktree.is_dir():
        name = record.results.get("feature_name")
        candidate = repo / ".worktrees" / name if name else None
        if candidate is not None and candidate.is_dir():
            patch["worktree"] = str(candidate.resolve())
            actions.append(f"worktree relocated to {candidate}")
            worktree = candidate
        else:
            actions.append(f"worktree {worktree} is missing and cannot be located")
    if worktree.is_dir():
        branch = current_branch(worktree)
        if branch != record.branch:
            patch["branch"] = branch
            actions.append(f"branch {record.branch} -> {branch}")
    if record.plan_file:
        plan = Path(record.plan_file)
        if plan.is_file():
            digest = hashlib.sha256(plan.read_bytes()).hexdigest()
            if record.plan_fingerprint and digest != record.plan_fingerprint:
                patch["plan_fingerprint"] = digest
                actions.append("plan fingerprint re-baselined")
        else:
            actions.append(f"plan {plan} is missing and cannot be restored")
    if record.config_fingerprint:
        try:
            current = config_fingerprint(_config_dir(record))
        except OSError:
            current = None
        if current is not None and current != record.config_fingerprint:
            patch["config_fingerprint"] = current
            actions.append("config fingerprint re-baselined")
    saved = record.results.get("workflow")
    if saved:
        try:
            config = load_config(_config_dir(record))
            catalog = config["customizations"]
            workflow = catalog.workflow(saved["name"])
            current_print = fingerprint(workflow, catalog)
        except (OSError, ValueError, KeyError):
            current_print = None
        if current_print is not None and current_print != saved.get("fingerprint"):
            patch["results"] = {"workflow": {**saved, "fingerprint": current_print}}
            actions.append("workflow fingerprint re-baselined")
    return patch, actions


def _required_session_roles(record: RunRecord, resume_at: str) -> set[str]:
    phases = {step.get("phase") for step in record.transitions}
    required = set()
    if "generator_started" in phases:
        required.add("generator")
    if "reviewer_started" in phases:
        required.add("reviewer")
    return required if resume_at == "review" else {"generator"} & required


def _tasks_unfinished(record: RunRecord) -> bool:
    phases = {step.get("phase") for step in record.transitions}
    return "tasks_integrated" not in phases and bool(
        phases & {"tasks_waiting", "tasks_running", "tasks_failed"}
    )


def _resume_at(record: RunRecord) -> str:
    if _tasks_unfinished(record):
        return "generate"  # replay unfinished slices; integrated ones are kept
    phase = record.phase
    if phase == PARKED_PHASE:  # continue from where the failure struck
        phase = str(record.park.get("phase_before") or phase)
    review_first = phase not in {"created", "preparing", "planning", "planned"}
    review_first = (
        review_first
        and bool(record.plan_file)
        and any(
            step.get("phase")
            in {
                "generator_started",
                "generator_finished",
                "reviewer_started",
                "reviewer_finished",
                "tester_finished",
                "interrupted_mutation",
                "checking",
                "checks_finished",
                "tasks_integrated",
            }
            for step in record.transitions
        )
    )
    saved_workflow = record.results.get("workflow")
    if saved_workflow:
        config = load_config(_config_dir(record))
        workflow = select_workflow(config, saved=saved_workflow)
        active = saved_workflow.get("loop") or saved_workflow.get("active")
        review_first = any(
            step.id == active and step.kind == "loop"
            for step in walk(expand(workflow, config["customizations"]))
        )
    return "review" if review_first else "generate"


def _driven_elsewhere(record: RunRecord) -> bool:
    """True while another live process still drives the run."""
    driver = record.driver if isinstance(record.driver, dict) else {}
    return driver.get("pid") != os.getpid() and bool(driver_alive(record))


def _launched_by_supervisor(record: RunRecord) -> bool:
    """True inside the relaunch the run's own live supervisor started."""
    nonce = os.environ.get(SUPERVISOR_ENV)
    return bool(nonce) and nonce == record.background.get("nonce")


_SUPERVISED = "its supervisor is still running"
# Skips that no later sweep can fix: the manager must stop and escalate.
_EXHAUSTED = frozenset({"max_total_resumes reached", "managed_resumes reached"})


def budget_refusal(record: RunRecord, config: dict) -> str | None:
    """Why no resume of this run can be attempted (not resumable, or its
    `max_total_resumes`/`managed_resumes` budget is spent), else None."""
    park = record.park if isinstance(record.park, dict) else {}
    checks = (
        # The native plan skill stops at `planned` on purpose: never continue it.
        (record.results.get("intent") == "plan", "plan-only native run"),
        (next_command(record) is None, f"{record.source} runs are rerun, not resumed"),
        (
            int(park.get("total_resumes", 0)) >= config["max_total_resumes"],
            "max_total_resumes reached",
        ),
        (
            int(park.get("manager_resumes", 0)) >= config["managed_resumes"],
            "managed_resumes reached",
        ),
    )
    return next((reason for hit, reason in checks if hit), None)


def _resume_budget_left(record: RunRecord, config: dict) -> str | None:
    """Why `resume --parked` must skip this record, or None to resume it."""
    if next_command(record) is not None and inspect_worker(
        record, include_terminal=True
    ).state in {"running", "starting"}:
        return _SUPERVISED
    return budget_refusal(record, config)


def _parked_run_ids(store: RunStore) -> list[str]:
    """Parked runs, oldest update first; unreadable records are skipped."""
    records = []
    for path in store.record_paths():
        with suppress(RunStateError):
            records.append(store.load(path.stem))
    return [
        record.id
        for record in sorted(records, key=lambda item: item.updated_at)
        if record.phase == PARKED_PHASE
    ]


async def _resume_one(working_dir: Path, store: RunStore, record: RunRecord) -> dict:
    """Continue one parked run (its output to stderr); summarize the result."""
    error = None
    with redirect_stdout(sys.stderr):
        try:
            code = await resume(
                working_dir,
                record.id,
                continue_run=True,
                repair=record.source == "native",
            )
        except Exception as exc:  # the run settles itself; keep going
            code, error = EXIT_BLOCKED, str(exc) or type(exc).__name__
    entry = {"run_id": record.id, "phase": store.load(record.id).phase, "exit": code}
    return {**entry, **({"error": error} if error else {})}


def _parked_exit_code(summary: dict) -> int:
    """1 a resumed run is blocked, or a run/queue item exhausted its budget
    (`stop: no_progress`); 75 work is still pending (a run left parked, a
    live supervisor, a requeued item); else 0."""
    phases = [entry["phase"] for entry in summary["resumed"]]
    if (
        any(entry["exhausted"] for entry in summary["skipped"])
        or summary["not_requeued"]
    ):
        summary["stop"] = "no_progress"
        return EXIT_BLOCKED
    if any(phase not in {"complete", "delivered", PARKED_PHASE} for phase in phases):
        return EXIT_BLOCKED
    if (
        PARKED_PHASE in phases
        or summary["requeued"]
        or any(entry["retryable"] for entry in summary["skipped"])
    ):
        return EXIT_TEMPFAIL
    return 0


def _park_orphaned_items(queue: QueueStore) -> None:
    """Park items a dead queue worker left `running`.

    Only `meow queue` used to notice them, so an unattended sweep reported
    "nothing to do" while the item stayed stuck. A live worker holds the lock
    and keeps its items.
    """
    if not queue.path.exists():
        return
    lock = QueueWorkerLock(queue.directory)
    if lock.acquire():
        try:
            queue.recover_orphans()
        finally:
            lock.release()


def _requeue_parked_items(working_dir: Path, store: RunStore, config: dict) -> dict:
    """Requeue parked queue items within budget and settle their old runs.

    A requeued item reruns from scratch, so its parked run record is settled
    (`cancelled`) instead of being reported by every later sweep.
    """
    queue = QueueStore(working_dir)
    try:
        _park_orphaned_items(queue)
        requeued = queue.requeue_parked(config["managed_resumes"])
        left = queue.parked()
    except QueueStateError:
        return {"requeued": [], "not_requeued": []}
    for item in requeued:
        with suppress(RunStateError):
            if item.run_id and store.load(item.run_id).phase == PARKED_PHASE:
                store.transition(
                    item.run_id,
                    "cancelled",
                    last_failure=f"Superseded: queue item {item.id} requeued",
                    results={"superseded_by": item.id},
                )
    return {
        "requeued": [item.id for item in requeued],
        "not_requeued": [item.id for item in left],
    }


async def resume_parked(working_dir: Path) -> int:
    """Continue every parked run within budget, then print a JSON summary.

    Parked queue items are requeued first (within `managed_resumes` attempts)
    for the next `meow queue` worker. Each record is reloaded just before it
    is resumed, since earlier resumes can take hours. Each run's own output
    goes to stderr so stdout stays one JSON document.
    """
    store = RunStore(working_dir)
    config = run_config(working_dir)
    summary: dict[str, list] = {"resumed": [], "skipped": []}
    summary.update(_requeue_parked_items(working_dir, store, config))
    for run_id in _parked_run_ids(store):
        try:
            record = store.load(run_id)
        except RunStateError:
            continue
        if record.phase != PARKED_PHASE:
            continue  # settled or resumed elsewhere meanwhile
        if reason := _resume_budget_left(record, config):
            summary["skipped"].append({
                "run_id": run_id,
                "reason": reason,
                "retryable": reason == _SUPERVISED,
                "exhausted": reason in _EXHAUSTED,
            })
        else:
            summary["resumed"].append(await _resume_one(working_dir, store, record))
    code = _parked_exit_code(summary)
    print(json.dumps(summary))
    return code


def _never_prepared(record: RunRecord) -> bool:
    """True for a background run that stopped before its worktree and branch
    existed (the record still says `preparing`): there is nothing to validate
    or replay, only the saved command to start again."""
    return never_prepared(record) and bool(record.background.get("argv"))


def _refuse(  # ruff: ignore[too-many-arguments]
    store: RunStore,
    record: RunRecord,
    message: str,
    *,
    permanent: bool,
    stop: str = "blocking",
) -> int:
    """Print why a resume cannot run and end the invocation with the contract
    line. A permanent refusal also settles a parked run `failed`
    (invalid_run_state), so a cron tick does not retry it forever."""
    from meow.cli.run_contract import _blocked

    print(message, file=sys.stderr)
    cause = "invalid_run_state"
    if permanent and record.phase == PARKED_PHASE:
        store.transition(
            record.id,
            "failed",
            last_failure=message,
            results={"failure": {"kind": "blocking", "cause": cause}},
        )
    _blocked(record.id, cause, message, stop)
    return 1


def _claim(store: RunStore, record: RunRecord) -> RunRecord | None:
    """Own the run before it looks active again, so a concurrent reconcile
    never mistakes this resume for the dead driver it replaces. A resume of a
    parked run that no supervisor launched spends one manager resume. None
    when the run changed or was taken over since it was read."""
    try:
        return store.claim_resume(
            record.id,
            phase=record.phase,
            count_manager=record.phase == PARKED_PHASE
            and not _launched_by_supervisor(record),
        )
    except RunStateError as exc:
        print(f"Cannot resume: {exc}", file=sys.stderr)
        return None


async def _restart_unprepared(
    working_dir: Path, store: RunStore, record: RunRecord
) -> int:
    print(
        f"Run {record.id} stopped before its worktree was prepared; "
        "starting its saved command again."
    )
    clear_cancel(store, record.id)
    if _claim(store, record) is None:
        return 1
    await redrive_saved_invocation(working_dir, record.id)
    return 0


async def resume(  # ruff: ignore[too-many-arguments, complex-structure, too-many-return-statements, too-many-statements, too-many-branches] -- inspect-first recovery with validation
    working_dir: Path,
    run_id: str | None = None,
    *,
    continue_run: bool = False,
    request: str | None = None,
    repair: bool = False,
    task_id: str | None = None,
) -> int:
    run_id = saved_run_id(working_dir, run_id)
    if run_id is not None and is_orchestrator_id(run_id):
        return await _resume_orchestration(
            working_dir,
            run_id,
            continue_run=continue_run,
            request=request,
            repair=repair,
            task_id=task_id,
        )
    if task_id is not None:
        print("--task requires an orchestration ID and --continue.", file=sys.stderr)
        return 1
    store = RunStore(working_dir)
    try:
        record = store.load(run_id) if run_id else store.latest()
    except RunStateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(render(record))
    if request is not None:
        print(
            "--request applies only to a blocked orchestration task.", file=sys.stderr
        )
        return 1
    if (continue_run or repair) and _driven_elsewhere(record):
        print(
            f"Cannot resume or repair: run {record.id} is still driven by live "
            f"process PID {record.driver.get('pid')}. Wait for it to stop "
            "before retrying.",
            file=sys.stderr,
        )
        return 1
    if (
        (continue_run or repair)
        and not _launched_by_supervisor(record)
        and inspect_worker(record, include_terminal=True).state
        in {"running", "starting"}
    ):
        print(
            "Cannot resume or repair while the background worker is running or "
            "starting. Wait for it to stop before retrying.",
            file=sys.stderr,
        )
        return 1
    if continue_run and _never_prepared(record):
        return await _restart_unprepared(working_dir, store, record)
    if repair and record.phase != "complete":
        patch, actions = _repair(record, working_dir)
        if patch:
            record = store.transition(record.id, record.phase, **patch)
        for action in actions:
            print(f"Repair: {action}.")
        if not actions:
            print("Repair: nothing to repair.")
    if not continue_run:
        return 0
    if record.phase == "complete":
        print("Run already complete.", file=sys.stderr)
        return 1
    if record.source not in RESUMABLE_SOURCES:
        return _refuse(
            store,
            record,
            f"Cannot resume {record.source} run with feature agents. "
            f"Inspect with meow status {record.id} and rerun its command.",
            permanent=False,
            stop="not_resumable",
        )
    reason = (
        budget_refusal(record, run_config(working_dir))
        if record.phase == PARKED_PHASE and not _launched_by_supervisor(record)
        else None
    )
    if reason:
        return _refuse(
            store,
            record,
            f"Cannot resume: {reason}.",
            permanent=False,
            stop="budget_exhausted" if reason in _EXHAUSTED else "not_resumable",
        )
    problem = _validate(record, working_dir)
    if problem:
        return _refuse(
            store,
            record,
            f"Cannot resume: {problem} Inspect with meow status {record.id}, "
            f"or re-baseline with meow resume {record.id} --continue --repair.",
            permanent=not problem.startswith("Repository mismatch"),
        )
    saved_workflow = record.results.get("workflow")
    resume_at = _resume_at(record)
    required_sessions = _required_session_roles(record, resume_at)
    missing_sessions = sorted(required_sessions - set(record.sessions))
    if missing_sessions and repair:
        print(
            "Repair: no Claude session for "
            f"{', '.join(missing_sessions)}; starting fresh session(s) from the plan."
        )
        required_sessions -= set(missing_sessions)
        missing_sessions = []
    if missing_sessions:
        return _refuse(
            store,
            record,
            "Cannot resume: missing Claude session reference(s) for "
            f"{', '.join(missing_sessions)}. Inspect with meow status "
            f"{record.id} and start a new run if the transcript is unavailable.",
            permanent=True,
        )
    clear_cancel(store, record.id)
    if (record := _claim(store, record)) is None:
        return 1
    await run_sprint(
        Path(record.worktree),
        record.results.get("feature_name"),
        record.request,
        use_worktree=False,
        plan_file=Path(record.plan_file) if record.plan_file else None,
        resume_at=resume_at,
        run_id=record.id,
        record_root=Path(record.repo),
        required_session_roles=required_sessions,
        workflow=saved_workflow["name"] if saved_workflow else "default",
    )
    return 0


async def _resume_orchestration(  # ruff: ignore[too-many-arguments] -- explicit lifecycle options
    working_dir: Path,
    run_id: str,
    *,
    continue_run: bool,
    request: str | None,
    repair: bool,
    task_id: str | None,
) -> int:
    problem = None
    if repair:
        problem = "--repair is only available for ordinary sprint runs."
    elif not continue_run and (task_id is not None or request is not None):
        problem = "--task and --request require --continue."
    if problem:
        print(problem, file=sys.stderr)
        return 1
    try:
        record = OrchestratorStore(working_dir).load(run_id)
    except (OrchestratorStateError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(render_status(record))
    if not continue_run:
        return 0
    return await resume_orchestration(
        working_dir, run_id, task_id=task_id, request=request
    )
