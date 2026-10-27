"""Supervisor: relaunch a crashed or parked run within the `[run]` budget.

The supervisor never drives agents itself. It runs the driver as a child in
its own process group (`meow _drive`, later `meow resume RUN_ID --continue`),
waits, reconciles the record, and relaunches while the run is parked,
retryable and within `auto_retries` / `max_total_resumes`.
"""

import os
import secrets
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from meow.common.process import process_identity
from meow.execution.run_failures import RESUMABLE_SOURCES
from meow.execution.run_state import PARKED_PHASE, RunRecord, RunStore, park_crashed
from meow.infrastructure.logging import get_logger
from meow.project.config import run_config

logger = get_logger(__name__)

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_TEMPFAIL = 75  # EX_TEMPFAIL: parked, `next_command` continues it
SUPERVISOR_ENV = "MEOW_SUPERVISOR_NONCE"
_SUCCESS_PHASES = frozenset({"complete", "delivered"})
_INTERRUPT_GRACE_SECONDS = 30
_WAIT_POLL_SECONDS = 0.5


def _sleep(seconds: float) -> None:
    """Wait before a relaunch (patched in tests)."""
    time.sleep(seconds)


def _meow(*args: str) -> list[str]:
    return [sys.executable, "-u", "-m", "meow", *args]


def _interrupt(process: subprocess.Popen) -> None:
    """Forward Ctrl-C to the child's own process group and let it settle."""
    with suppress(OSError):
        if sys.platform == "win32":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(process.pid, signal.SIGINT)
    try:
        process.wait(timeout=_INTERRUPT_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _run_child(repo: Path, command: list[str], env: dict[str, str]) -> int:
    """Run one driver in its own process group and return its exit code.

    A stray signal to the driver's group leaves the supervisor alive to
    reconcile and relaunch.
    """
    windows = sys.platform == "win32"
    process = subprocess.Popen(
        command,
        cwd=repo,
        env=env,
        stdin=subprocess.DEVNULL,
        start_new_session=not windows,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if windows else 0,
    )
    try:
        while True:
            # A bounded wait: on Windows an unbounded one never sees Ctrl-C.
            with suppress(subprocess.TimeoutExpired):
                return process.wait(timeout=_WAIT_POLL_SECONDS)
    except KeyboardInterrupt:
        _interrupt(process)
        raise


def relaunch_delay(record: RunRecord, config: dict) -> float | None:
    """Seconds to wait before resuming a parked run; None past the budget.

    A park's `retry_after` (rate limit, budget reset) wins over
    `retry_backoff[attempts-1]`.
    """
    park = record.park if isinstance(record.park, dict) else {}
    attempts = max(1, int(park.get("attempts", 1)))
    if (
        record.phase != PARKED_PHASE
        or park.get("kind") == "blocking"
        or record.source not in RESUMABLE_SOURCES
        or attempts > config["auto_retries"]
        or int(park.get("total_resumes", 0)) >= config["max_total_resumes"]
    ):
        return None
    retry_after = park.get("retry_after")
    if isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool):
        return max(0.0, float(retry_after))
    backoff = config["retry_backoff"]
    return float(backoff[min(attempts, len(backoff)) - 1])


def exit_code(record: RunRecord) -> int:
    """0 settled success, 75 parked (resumable), 1 anything else."""
    if record.phase in _SUCCESS_PHASES:
        return EXIT_OK
    return EXIT_TEMPFAIL if record.phase == PARKED_PHASE else EXIT_BLOCKED


def supervise(repo: Path, run_id: str, nonce: str) -> int:
    """Drive `run_id` through `_drive`, then relaunch it while it parks.

    Returns 0 for `complete`/`delivered`, 75 when left parked, 1 when blocked.
    """
    repo = Path(repo).resolve()
    store = RunStore(repo)
    env = {**os.environ, "PYTHONUNBUFFERED": "1", SUPERVISOR_ENV: nonce}
    command = _meow("_drive", run_id, "--work-dir", str(repo), "--nonce", nonce)
    last_park = None
    while True:
        code = _run_child(repo, command, env)
        # The child exited: anything it left unsettled died with it.
        record = park_crashed(store, run_id) or store.load(run_id)
        logger.info(
            "supervisor_child_exited", run=run_id, code=code, phase=record.phase
        )
        if record.phase != PARKED_PHASE:
            return exit_code(record)
        if last_park is not None and record.park.get("parked_at") == last_park:
            return EXIT_TEMPFAIL  # the resume refused to start; nothing new
        delay = relaunch_delay(record, run_config(repo))
        if delay is None:
            return EXIT_TEMPFAIL
        print(
            f"[supervisor] run {run_id} parked ({record.park.get('cause')}); "
            f"resuming in {delay:g}s",
            flush=True,
        )
        _sleep(delay)
        record = store.load(run_id)  # cancelled or resumed meanwhile?
        if record.phase != PARKED_PHASE:
            return exit_code(record)
        last_park = record.park.get("parked_at")
        command = _meow("resume", run_id, "--continue", "--work-dir", str(repo))


def run_supervised(
    repo: Path, argv: list[str], on_created: Callable[[str], None] | None = None
) -> int:
    """Foreground `meow run` under a supervisor in this process.

    The run is checkpointed like a background run (this process registers as
    its worker), so `_drive` and `meow resume` continue it the same way.
    `on_created` receives the new run ID.
    """
    from meow.cli.parser import _build_arg_parser
    from meow.execution.sprint_runner import _settle_failure
    from meow.infrastructure.background import _create_run, _notify_once

    saved = argv if "--background" in argv else [*argv, "--background"]
    repo = Path(repo).resolve()
    store = RunStore(repo)
    record = _create_run(store, repo, _build_arg_parser().parse_args(saved), None)
    if on_created is not None:
        on_created(record.id)
    nonce = secrets.token_hex(16)
    store.update_background(
        record.id,
        argv=saved,
        nonce=nonce,
        launch_started_at=time.time(),
        pid=os.getpid(),
        start_identity=process_identity(os.getpid()),
        state="running",
        supervisor="foreground",
    )
    print(f"[run] {record.id} supervised; inspect: meow status {record.id}", flush=True)
    try:
        return supervise(repo, record.id, nonce)
    except BaseException as exc:
        _settle_failure(store, record.id, exc)
        raise
    finally:
        _notify_once(store, record.id)


def should_supervise(repo: Path) -> bool:
    """Foreground runs are supervised under Claude Code or `[run] supervise`."""
    if os.environ.get("CLAUDECODE") and not os.environ.get(SUPERVISOR_ENV):
        return True
    try:
        return bool(run_config(repo)["supervise"]) and not os.environ.get(
            SUPERVISOR_ENV
        )
    except (OSError, ValueError):
        return False
