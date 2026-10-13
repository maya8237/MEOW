"""Local detached workers for unattended feature runs."""

import argparse
import asyncio
import ctypes
import hashlib
import os
import secrets
import subprocess
import sys
import time
import tomllib
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from meow.execution.run_state import TERMINAL_PHASES, RunRecord, RunStore
from meow.project.config import _merge_config, config_paths
from meow.project.config_env import _expand_config_environment

_STARTUP_GRACE_SECONDS = 10


class BackgroundError(RuntimeError):
    """A worker could not be launched or validated."""


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


def _process_identity(  # ruff: ignore[too-many-return-statements, complex-structure, too-many-branches, too-many-statements]
    pid: int,
) -> str | None:
    """Return an OS process birth identity so PID reuse never counts as live."""
    if sys.platform == "win32":
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            created = wintypes.FILETIME()
            exited = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            if not kernel.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                return None
            return f"{created.dwHighDateTime}:{created.dwLowDateTime}"
        finally:
            kernel.CloseHandle(handle)
    stat = Path(f"/proc/{pid}/stat")
    if stat.is_file():
        try:
            # Field 22 is the process start tick; the command field may contain spaces.
            return stat.read_text(encoding="utf-8").rsplit(")", 1)[1].split()[19]
        except (OSError, IndexError):
            return None
    try:
        ps = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart=", "-o", "args="],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if ps.returncode == 0 and ps.stdout.strip():
        return hashlib.sha256(ps.stdout.strip().encode("utf-8")).hexdigest()
    return None


def inspect_worker(  # ruff: ignore[too-many-return-statements]
    record: RunRecord,
) -> WorkerState:
    data = record.background if isinstance(record.background, dict) else {}
    pid = data.get("pid")
    log = data.get("log")
    if not data:
        return WorkerState("not_background", None, None)
    if record.phase in TERMINAL_PHASES:
        return WorkerState(record.phase, pid if isinstance(pid, int) else None, log)
    expected = data.get("start_identity")
    if not isinstance(pid, int) or not isinstance(expected, str) or not expected:
        started = data.get("launch_started_at")
        if (
            isinstance(started, (int, float))
            and time.time() - started > _STARTUP_GRACE_SECONDS
        ):
            return WorkerState("interrupted", None, log)
        return WorkerState("starting", None, log)
    actual = _process_identity(pid)
    return WorkerState("running" if actual == expected else "interrupted", pid, log)


def reconcile_worker(store: RunStore, run_id: str) -> WorkerState:
    record = store.load(run_id)
    state = inspect_worker(record)
    if state.state == "interrupted" and record.phase not in {
        *TERMINAL_PHASES,
        "interrupted_mutation",
    }:
        store.transition(
            run_id,
            "interrupted_mutation",
            last_failure="Background worker exited; inspect worktree before resume",
        )
    return state


def _invocation_kind(
    args: argparse.Namespace,
) -> Literal["run", "orchestrator-task"]:
    """Classify a background invocation; reject anything else."""
    if args.command == "_orchestrator-task":
        return "orchestrator-task"
    if args.command != "run" or not args.unattended or not args.background:
        raise BackgroundError(
            "Background execution requires run --unattended --background"
        )
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
    return store.create(
        source="jira" if args.jira is not None else "prompt",
        request=args.request or args.jira or "latest Jira issue",
        repo=repo,
        worktree=repo,
        branch="preparing",
    )


def launch_background(
    repo: Path, argv: list[str], *, seed: RunSeed | None = None
) -> str:
    """Checkpoint an unattended invocation and start one detached worker.

    `run --unattended --background` creates its own run record. The internal
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
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
            | getattr(subprocess, "DETACHED_PROCESS", 0)
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


def worker_main(  # ruff: ignore[too-many-statements]
    repo: Path, run_id: str, nonce: str
) -> int:
    """Run saved invocation in the detached process, never prompting."""
    from meow.cli.parser import _build_arg_parser
    from meow.cli.validation import _resolve_input_path, parse_inputs
    from meow.execution.sprint_runner import run_sprint
    from meow.integrations.issue_solver import run_issue_solver

    store = RunStore(repo)
    try:
        record = _wait_for_registration(store, run_id, nonce)
        args = _build_arg_parser().parse_args(record.background["argv"])
        try:
            kind = _invocation_kind(args)
        except BackgroundError as exc:
            raise BackgroundError("Saved worker invocation is invalid") from exc
        if kind == "orchestrator-task":
            from meow.execution.orchestration.task_worker import run_task_attempt

            asyncio.run(run_task_attempt(repo, run_id, args))
        elif args.jira is not None:
            asyncio.run(
                run_issue_solver(
                    repo,
                    args.jira or None,
                    test=args.test,
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
                    run_id=run_id,
                    record_root=repo,
                    unattended=True,
                    **({"workflow": args.workflow} if args.workflow else {}),
                    **({"inputs": parse_inputs(args.input)} if args.input else {}),
                )
            )
        return 0
    except BaseException as exc:
        if store.load(run_id).phase not in TERMINAL_PHASES:
            store.transition(run_id, "failed", last_failure=f"Worker failed: {exc}")
        raise
    finally:
        _notify_once(store, run_id)
