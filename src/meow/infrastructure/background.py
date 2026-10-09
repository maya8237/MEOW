"""Local detached workers for unattended feature runs."""

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

from meow.execution.run_state import RunRecord, RunStore
from meow.project.config import _expand_config_environment, _merge_config, config_paths

# Keep the old ``background.core`` patch point working for existing callers.
core = sys.modules[__name__]

_TERMINAL = {
    "complete",
    "failed",
    "cancelled",
    "exhausted",
    "delivery_failed",
    "needs_user_decision",
}
_STARTUP_GRACE_SECONDS = 10


class BackgroundError(RuntimeError):
    """A worker could not be launched or validated."""


@dataclass(frozen=True)
class WorkerState:
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
    if record.phase in _TERMINAL:
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
        *_TERMINAL,
        "interrupted_mutation",
    }:
        store.transition(
            run_id,
            "interrupted_mutation",
            last_failure="Background worker exited; inspect worktree before resume",
        )
    return state


def launch_background(  # ruff: ignore[too-many-statements]
    repo: Path, argv: list[str]
) -> str:
    """Checkpoint an unattended invocation and start one detached worker."""
    from meow.cli.cli import _build_arg_parser

    args = _build_arg_parser().parse_args(argv)
    if args.command != "run" or not args.unattended or not args.background:
        raise BackgroundError(
            "Background execution requires run --unattended --background"
        )
    if args.lint_fix or args.manually_approve_plan:
        raise BackgroundError("Background execution cannot prompt or run lint-fix")
    repo = repo.resolve()
    store = RunStore(repo)
    record = store.create(
        source="jira" if args.jira is not None else "prompt",
        request=args.request or args.jira or "latest Jira issue",
        repo=repo,
        worktree=repo,
        branch="preparing",
    )
    nonce = secrets.token_hex(16)
    log_path = store.directory / f"{record.id}.log"
    store.update_background(
        record.id,
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
        "from meow.cli import cli_main; cli_main()",
        "_worker",
        record.id,
        "--working-dir",
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
        store.update_background(record.id, launcher_pid=process.pid, state="starting")
    except (OSError, BackgroundError) as exc:
        if process is not None and process.poll() is None:
            process.terminate()
        store.transition(
            record.id, "failed", last_failure=f"Background launch failed: {exc}"
        )
        raise BackgroundError(f"Background launch failed: {exc}") from exc
    return record.id


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
    if record.phase not in _TERMINAL or record.background.get("notification_claimed"):
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
    from meow.cli.cli import _build_arg_parser, _resolve_input_path
    from meow.execution.sprint_runner import run_sprint
    from meow.integrations.issue_solver import run_issue_solver

    store = RunStore(repo)
    try:
        record = _wait_for_registration(store, run_id, nonce)
        args = _build_arg_parser().parse_args(record.background["argv"])
        if args.command != "run" or not args.unattended or not args.background:
            raise BackgroundError("Saved worker invocation is invalid")
        if args.jira is not None:
            asyncio.run(
                run_issue_solver(repo, args.jira or None, test=args.test, run_id=run_id)
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
                )
            )
        return 0
    except BaseException as exc:
        if store.load(run_id).phase not in _TERMINAL:
            store.transition(run_id, "failed", last_failure=f"Worker failed: {exc}")
        raise
    finally:
        _notify_once(store, run_id)
