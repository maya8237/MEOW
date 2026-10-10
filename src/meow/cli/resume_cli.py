"""Inspect and explicitly resume a saved run after identity validation."""

import hashlib
import subprocess
import sys
from pathlib import Path

from meow.cli.status_cli import render
from meow.execution.run_state import RunRecord, RunStateError, RunStore
from meow.execution.sprint_runner import run_sprint
from meow.infrastructure.cancellation import clear_cancel
from meow.infrastructure.checks import config_fingerprint
from meow.infrastructure.worktree import current_branch


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
            current = config_fingerprint(repo)
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
    return None


def _partial_tasks(record: RunRecord) -> bool:
    phases = {step.get("phase") for step in record.transitions}
    return "tasks_integrated" not in phases and bool(
        phases.intersection({"tasks_waiting", "tasks_running"})
    )


def _required_session_roles(record: RunRecord, resume_at: str) -> set[str]:
    phases = {step.get("phase") for step in record.transitions}
    required = set()
    if "generator_started" in phases:
        required.add("generator")
    if "reviewer_started" in phases:
        required.add("reviewer")
    return required if resume_at == "review" else {"generator"} & required


async def resume(  # ruff: ignore[complex-structure, too-many-return-statements, too-many-statements]
    working_dir: Path,
    run_id: str | None = None,
    *,
    continue_run: bool = False,
    auto_resume: bool = False,
) -> int:
    store = RunStore(working_dir)
    try:
        record = store.load(run_id) if run_id else store.latest()
    except RunStateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(render(record))
    if not (continue_run or auto_resume):
        return 0
    if record.phase == "complete":
        print("Run already complete.", file=sys.stderr)
        return 1
    if record.source not in {"prompt", "jira", "native"}:
        print(
            f"Cannot resume {record.source} run with feature agents. "
            f"Inspect with meow status {record.id} and rerun its command.",
            file=sys.stderr,
        )
        return 1
    problem = _validate(record, working_dir)
    if problem:
        print(
            f"Cannot resume: {problem} Inspect with meow status {record.id}.",
            file=sys.stderr,
        )
        return 1
    if _partial_tasks(record):
        print(
            "Cannot replay a partially integrated task graph safely. "
            "Preserve the task worktrees and inspect their commits with "
            f"meow status {record.id} --verbose; review and integrate the "
            "completed slices before starting a new run.",
            file=sys.stderr,
        )
        return 1
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
    resume_at = "review" if review_first else "generate"
    required_sessions = _required_session_roles(record, resume_at)
    missing_sessions = sorted(required_sessions - set(record.sessions))
    if missing_sessions:
        print(
            "Cannot resume: missing Claude session reference(s) for "
            f"{', '.join(missing_sessions)}. Inspect with meow status "
            f"{record.id} and start a new run if the transcript is unavailable.",
            file=sys.stderr,
        )
        return 1
    clear_cancel(store, record.id)
    store.transition(record.id, "resuming", attempt=record.attempt + 1)
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
    )
    return 0
