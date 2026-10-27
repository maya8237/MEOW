"""Local detached workers for automatic feature runs."""

import argparse
import asyncio
import os
import secrets
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from meow.common.process import process_identity as _process_identity
from meow.execution.run_failures import BlockingRunError
from meow.execution.run_state import (
    PARKED_PHASE,
    TERMINAL_PHASES,
    RunRecord,
    RunStore,
    park_crashed,
    run_owner_alive,
    startup_grace,
)
from meow.project.config import _merge_config, config_paths
from meow.project.config_env import _expand_config_environment

# Phases no worker drives any more; a parked run waits for `meow resume`.
_SETTLED = TERMINAL_PHASES | {PARKED_PHASE}


class BackgroundError(RuntimeError):
    """A worker could not be launched or validated."""


class InvalidInvocationError(BackgroundError, BlockingRunError):
    """A saved worker invocation no resume can repair."""


@dataclass(frozen=True)
class RunSeed:
    """What the orchestrator knows about a child run before its worker starts."""

    source: str
    request: str
    worktree: Path
    branch: str


@dataclass(frozen=True)
class WorkerState:
    state: str
    state: str
    pid: int | None
    log: str | None


def inspect_worker(  # ruff: ignore[too-many-return-statements]
    record: RunRecord, *, include_terminal: bool = False
) -> WorkerState:
    data = record.background if isinstance(record.background, dict) else {}
    pid = data.get("pid")
    log = data.get("log")
    if not data:
        return WorkerState("not_background", None, None)
    if record.phase in _SETTLED and not include_terminal:
        return WorkerState(record.phase, pid if isinstance(pid, int) else None, log)
    expected = data.get("start_identity")
    if not isinstance(pid, int) or not isinstance(expected, str) or not expected:
        started = data.get("launch_started_at")
        if isinstance(started, (int, float)) and time.time() - started > startup_grace(
            Path(record.repo)
        ):
            return WorkerState("interrupted", None, log)
        return WorkerState("starting", None, log)
    actual = _process_identity(pid)
    return WorkerState("running" if actual == expected else "interrupted", pid, log)


def reconcile_worker(store: RunStore, run_id: str) -> WorkerState:
    """Report the worker state; park the run if its worker and driver died."""
    record = store.load(run_id)
    state = inspect_worker(record)
    if record.phase not in _SETTLED and run_owner_alive(record) is False:
        park_crashed(store, run_id)
    return state


def _invocation_kind(
    args: argparse.Namespace,
) -> Literal["run", "orchestrator-task"]:
    """Classify a background invocation; reject anything else."""
    if args.command == "_orchestrator-task":
        return "orchestrator-task"
    if args.command != "run" or not args.background:
        raise BackgroundError("Background execution requires run --background")
    if args.lint_fix or args.manually_approve_plan:
        raise BackgroundError("Background execution cannot prompt or run lint-fix")
    return "run"


def _create_run(
    store: RunStore,
    repo: Path,
    args: argparse.Namespace,
    seed: RunSeed | None,
) -> RunRecord:
    if _invocation_kind(args) == "orchestrator-task":
        if seed is None:
            raise BackgroundError("An orchestrator task launch requires a run seed")
        return store.create(
            source=seed.source,
            request=seed.request,
            repo=repo,
            worktree=seed.worktree,
            branch=seed.branch,
        )
    if seed is not None:
        raise BackgroundError("A run launch does not take a run seed")
    from meow.cli.validation import pipeline_overrides
    from meow.project.config import load_config

    delivery = (
        {"enabled": args.push is not None, "push": args.push is True}
        if args.no_worktree
        else {}
    )
    if not args.no_worktree and args.jira is None:
        delivery.update(
            base=args.target_branch or load_config(repo)["delivery"]["target_branch"],
            source_branch=args.source_branch,
            pipeline_options=pipeline_overrides(args),
        )
    return store.create(
        source="jira" if args.jira is not None else "prompt",
        request=args.request or args.jira or "latest Jira issue",
        repo=repo,
        worktree=repo,
        branch="preparing",
        delivery=delivery,
    )


def launch_background(
    repo: Path, argv: list[str], *, seed: RunSeed | None = None
) -> str:
    """Checkpoint an automatic invocation and start one detached worker.

    `run --background` creates its own run record. The internal
    `_orchestrator-task` invocation needs a `seed` describing the run record.
    """
    from meow.cli.parser import _build_arg_parser

    args = _build_arg_parser().parse_args(argv)
    repo = repo.resolve()
    store = RunStore(repo)
    record = _create_run(store, repo, args, seed)
    _spawn_worker(store, repo, record.id, argv)
    return record.id


def _spawn_worker(store: RunStore, repo: Path, run_id: str, argv: list[str]) -> None:
    nonce = secrets.token_hex(16)
    log_path = store.directory / f"{run_id}.log"
    store.update_background(
        run_id,
        argv=argv,
        nonce=nonce,
        log=str(log_path),
        launch_started_at=time.time(),
    )
    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    command = [
        sys.executable,
        "-u",
        "-c",
        "from meow.cli.cli import cli_main; cli_main()",
        "_worker",
        run_id,
        "--work-dir",
        str(repo),
        "--nonce",
        nonce,
    ]
    process = None
    try:
        with log_path.open("ab") as stream:
            process = subprocess.Popen(
                command,
                cwd=repo,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=sys.platform != "win32",
                creationflags=creationflags,
                close_fds=True,
            )
        store.update_background(run_id, launcher_pid=process.pid, state="starting")
    except (OSError, BackgroundError) as exc:
        if process is not None and process.poll() is None:
            process.terminate()
        store.transition(
            run_id, "failed", last_failure=f"Background launch failed: {exc}"
        )
        raise BackgroundError(f"Background launch failed: {exc}") from exc


def detach_command(
    working_dir: Path, argv: list[str], log: str | None = None
) -> dict[str, object]:
    """Start `argv` in its own session/process group so nothing kills it.

    The process keeps running after the caller exits, is interrupted, or has
    its context compacted. Output goes to a log file; poll it and the PID.
    """
    argv = argv[1:] if argv[:1] == ["--"] else list(argv)
    if not argv:
        raise BackgroundError("Nothing to run: meow detach [--log FILE] -- COMMAND ...")
    working_dir = working_dir.resolve()
    if log is None:
        directory = working_dir / ".meow" / "detached"
        directory.mkdir(parents=True, exist_ok=True)
        log_path = directory / f"{int(time.time())}-{secrets.token_hex(3)}.log"
    else:
        log_path = Path(log).resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
    creationflags = 0
    if sys.platform == "win32":
        # Not DETACHED_PROCESS: it overrides CREATE_NO_WINDOW, and a venv
        # python.exe then spawns its base interpreter in a new visible console.
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    with log_path.open("ab") as stream:
        process = subprocess.Popen(
            argv,
            cwd=working_dir,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=sys.platform != "win32",
            creationflags=creationflags,
            close_fds=True,
        )
    return {"pid": process.pid, "log": str(log_path), "argv": argv}


def _wait_for_registration(store: RunStore, run_id: str, nonce: str) -> RunRecord:
    for _ in range(100):
        record = store.load(run_id)
        data = record.background
        if data.get("nonce") != nonce:
            raise BackgroundError("Worker nonce does not match saved run")
        if isinstance(data.get("launcher_pid"), int):
            identity = _process_identity(os.getpid())
            if identity is None:
                raise BackgroundError("Worker process identity unavailable")
            return store.update_background(
                run_id, pid=os.getpid(), start_identity=identity, state="running"
            )
        time.sleep(0.1)
    raise BackgroundError("Worker registration timed out")


def _notify_once(  # ruff: ignore[complex-structure, too-many-return-statements]
    store: RunStore, run_id: str
) -> None:
    """Run an optional local notifier with only sanitized summary fields."""
    record = store.load(run_id)
    if record.phase not in TERMINAL_PHASES or record.background.get(
        "notification_claimed"
    ):
        return
    config_files = config_paths(Path(record.repo))
    if not config_files:
        return
    try:
        raw = {}
        for config_path in reversed(config_files):
            raw = _merge_config(
                raw,
                _expand_config_environment(
                    tomllib.loads(config_path.read_text(encoding="utf-8"))
                ),
            )
        argv = raw.get("background", {}).get("notify_command")
    except (OSError, ValueError, AttributeError):
        return
    if argv is None:
        return
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(item, str) and item for item in argv)
    ):
        store.update_background(run_id, notification="invalid configuration")
        return
    store.update_background(run_id, notification_claimed=True)
    try:
        result = subprocess.run(
            [*argv, run_id, record.phase, f"meow status {run_id}"],
            cwd=record.repo,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        outcome = "sent" if result.returncode == 0 else f"failed ({result.returncode})"
    except (OSError, subprocess.TimeoutExpired) as exc:
        outcome = f"failed ({type(exc).__name__})"
    store.update_background(run_id, notification=outcome)


def _saved_invocation(record: RunRecord) -> argparse.Namespace:
    """Parse and validate the saved worker argv; reject anything unexpected."""
    from meow.cli.parser import _build_arg_parser

    args = _build_arg_parser().parse_args(record.background["argv"])
    try:
        _invocation_kind(args)
    except BackgroundError as exc:
        raise InvalidInvocationError(
            "Saved worker invocation is invalid", cause="invalid_input"
        ) from exc
    return args


def worker_main(repo: Path, run_id: str, nonce: str) -> int:
    """The detached worker: register, then supervise the run's driver.

    The driver runs as a `_drive` child (see `supervisor.supervise`), which
    relaunches a parked run within the `[run]` retry budget.
    """
    from meow.execution.sprint_runner import _settle_failure
    from meow.infrastructure.supervisor import supervise

    store = RunStore(repo)
    try:
        _saved_invocation(_wait_for_registration(store, run_id, nonce))
        return supervise(repo, run_id, nonce)
    except BaseException as exc:
        _settle_failure(store, run_id, exc)  # park unless blocking
        raise
    finally:
        _notify_once(store, run_id)


def drive_main(repo: Path, run_id: str, nonce: str) -> int:
    """Run the saved invocation in this process (the supervised driver)."""
    from meow.execution.sprint_runner import _settle_failure

    store = RunStore(repo)
    try:
        record = store.load(run_id)
        if record.background.get("nonce") != nonce:
            raise BackgroundError("Driver nonce does not match saved run")
        _drive_invocation(repo, run_id, _saved_invocation(record))
        return 0
    except BaseException as exc:
        _settle_failure(store, run_id, exc)  # park unless blocking
        raise


def _drive_invocation(repo: Path, run_id: str, args: argparse.Namespace) -> None:
    from meow.cli.validation import (
        _resolve_input_path,
        parse_inputs,
        pipeline_overrides,
    )
    from meow.execution.sprint_runner import run_sprint
    from meow.integrations.issue_solver import run_issue_solver

    if _invocation_kind(args) == "orchestrator-task":
        from meow.execution.orchestration.task_worker import run_task_attempt

        asyncio.run(run_task_attempt(repo, run_id, args))
    elif args.jira is not None:
        asyncio.run(
            run_issue_solver(
                repo,
                args.jira or None,
                test=args.test,
                **({"force_disable_tests": True} if args.force_disable_tests else {}),
                run_id=run_id,
                **({"workflow": args.workflow} if args.workflow else {}),
                **({"inputs": parse_inputs(args.input)} if args.input else {}),
            )
        )
    else:
        asyncio.run(
            run_sprint(
                repo,
                args.feature_name,
                args.request,
                use_worktree=not args.no_worktree,
                plan_file=_resolve_input_path(args.plan, repo),
                source_branch=args.source_branch,
                resume_at=args.resume_at,
                test=args.test,
                **({"force_disable_tests": True} if args.force_disable_tests else {}),
                run_id=run_id,
                record_root=repo,
                **(
                    {"commit": args.push is not None, "push": args.push is True}
                    if args.no_worktree
                    else {}
                ),
                **({"workflow": args.workflow} if args.workflow else {}),
                **({"inputs": parse_inputs(args.input)} if args.input else {}),
                **(
                    {"pipeline_options": opts}
                    if (opts := pipeline_overrides(args))
                    else {}
                ),
            )
        )
