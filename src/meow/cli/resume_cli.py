"""Inspect and explicitly resume a saved run after identity validation."""

import hashlib
import subprocess
import sys
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
from meow.execution.run_state import RunRecord, RunStateError, RunStore
from meow.execution.sprint_runner import run_sprint
from meow.infrastructure.background import inspect_worker
from meow.infrastructure.cancellation import clear_cancel
from meow.infrastructure.checks import config_fingerprint
from meow.infrastructure.worktree import current_branch
from meow.project.config import config_root, load_config
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
    review_first = record.phase not in {"created", "preparing", "planning", "planned"}
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
    if (continue_run or repair) and inspect_worker(
        record, include_terminal=True
    ).state in {"running", "starting"}:
        print(
            "Cannot resume or repair while the background worker is running or "
            "starting. Wait for it to stop before retrying.",
            file=sys.stderr,
        )
        return 1
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
        print(
            f"Cannot resume {record.source} run with feature agents. "
            f"Inspect with meow status {record.id} and rerun its command.",
            file=sys.stderr,
        )
        return 1
    problem = _validate(record, working_dir)
    if problem:
        print(
            f"Cannot resume: {problem} Inspect with meow status {record.id}, "
            f"or re-baseline with meow resume {record.id} --continue --repair.",
            file=sys.stderr,
        )
        return 1
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
        print(
            "Cannot resume: missing Claude session reference(s) for "
            f"{', '.join(missing_sessions)}. Inspect with meow status "
            f"{record.id} and start a new run if the transcript is unavailable.",
            file=sys.stderr,
        )
        return 1
    clear_cancel(store, record.id)
    record = store.transition(record.id, "resuming", attempt=record.attempt + 1)
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
