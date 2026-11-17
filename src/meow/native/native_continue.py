"""`meow native continue`: keep a native run going after its session stops.

Claude Code fires `StopFailure` when a turn ends on an API error (rate limit,
overload, server error, ...). The plugin hook passes that payload here; when
the stopped session was driving a native `--intent run` record, a detached
supervisor resumes the session with `claude -p --resume ID --fork-session` and
tells it to continue the run through `/meow:run` until the workflow settles.

The supervisor owns the run as its `driver` (host `native-continue`), so CLI
resumes and the stopped interactive session are refused while it works; only
its child sessions (`MEOW_CONTINUE_RUN`) may checkpoint. Success (`complete`
or `delivered`) is the only outcome that ends it: a `failed`, `exhausted` or
`delivery_failed` run is reopened and relaunched, without limit unless a cap
is given (`--cap N` or `[run] native_continue_cap`). `meow cancel RUN_ID`
cancels the run and its session; `meow native continue RUN_ID --stop` only
stops supervising, leaving the run resumable.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

from meow.common.process import kill_process_tree, process_identity
from meow.execution.run_failures import progress_fingerprint
from meow.execution.run_state import (
    PARKED_PHASE,
    TERMINAL_PHASES,
    RunRecord,
    RunStateError,
    RunStore,
    driver_alive,
)
from meow.infrastructure.background import detach_command
from meow.infrastructure.cancellation import cancel_requested
from meow.project.config import run_config

SESSION_ENV = "CLAUDE_CODE_SESSION_ID"
CONTINUE_ENV = "MEOW_CONTINUE_RUN"
HOST = "native-continue"
MAX_BACKOFF = 1800.0
SUCCESS_PHASES = frozenset({"complete", "delivered"})
_POLL_SECONDS = 5.0
# Modes that cannot edit unattended: the child then runs with its own default.
_SKIPPED_MODES = frozenset({"plan"})


def continuing(record: RunRecord) -> bool:
    """True while a live `native continue` supervisor drives this run."""
    driver = record.driver if isinstance(record.driver, dict) else {}
    return driver.get("host") == HOST and driver_alive(record) is True


def child_of_supervisor(record: RunRecord) -> bool:
    """This process is a session the run's own supervisor launched."""
    return os.environ.get(CONTINUE_ENV) == record.id and continuing(record)


def unlimited(record: RunRecord) -> bool:
    """A live supervisor without a cap: parks never end the run on no progress."""
    settings = record.results.get("auto_continue")
    return continuing(record) and isinstance(settings, dict) and not settings.get("cap")


def _session_runs(repo: Path, session_id: str) -> list[RunRecord]:
    store = RunStore(repo)
    found = []
    for path in store.record_paths():
        with suppress(RunStateError, OSError, ValueError):
            record = store.load(path.stem)
            if (
                record.source == "native"
                and record.results.get("intent") == "run"
                and record.results.get("claude_session_id") == session_id
                and record.phase not in SUCCESS_PHASES | {"cancelled"}
            ):
                found.append(record)
    return sorted(found, key=lambda item: item.updated_at, reverse=True)


def _repos(cwd: Path) -> list[Path]:
    """Directories from `cwd` upward that hold a run journal."""
    return [path for path in (cwd, *cwd.parents) if (path / ".meow" / "runs").is_dir()]


def from_hook(working_dir: Path, payload: dict) -> dict:  # ruff: ignore[too-many-return-statements]
    """Handle a `StopFailure` payload: start a supervisor for the session's run."""
    if os.environ.get(CONTINUE_ENV):
        return {"started": False, "reason": "supervised_child"}
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return {"started": False, "reason": "no_session"}
    cwd = Path(str(payload.get("cwd") or working_dir)).resolve()
    for repo in dict.fromkeys([*_repos(cwd), *_repos(working_dir.resolve())]):
        runs = _session_runs(repo, session_id)
        if not runs:
            continue
        if not run_config(repo)["native_auto_continue"]:
            return {"started": False, "reason": "disabled", "run_id": runs[0].id}
        return start(
            repo,
            runs[0].id,
            session_id=session_id,
            permission_mode=payload.get("permission_mode"),
            error=payload.get("error"),
        )
    return {"started": False, "reason": "no_native_run"}


def start(  # ruff: ignore[complex-structure, too-many-arguments]
    repo: Path,
    run_id: str,
    *,
    session_id: str | None = None,
    permission_mode: object = None,
    cap: int | None = None,
    error: object = None,
) -> dict:
    """Detach `meow native continue RUN_ID --supervise` and return at once."""
    record = RunStore(repo).load(run_id)
    if continuing(record):
        return {"started": False, "reason": "already_continuing", "run_id": run_id}
    if record.phase in SUCCESS_PHASES | {"cancelled"}:
        return {"started": False, "reason": record.phase, "run_id": run_id}
    _stop_marker(RunStore(repo), run_id).unlink(missing_ok=True)
    argv = [
        sys.executable,
        "-c",
        "from meow.cli.cli import cli_main; cli_main()",
        "native",
        "continue",
        run_id,
        "--supervise",
        "--work-dir",
        str(repo),
    ]
    if session_id:
        argv += ["--session", session_id]
    if isinstance(permission_mode, str) and permission_mode:
        argv += ["--permission-mode", permission_mode]
    if cap is not None:
        argv += ["--cap", str(cap)]
    if isinstance(error, str) and error:
        argv += ["--error", error]
    log = RunStore(repo).directory / f"{run_id}.continue.log"
    detached = detach_command(repo, argv, str(log), env=dict(os.environ))
    return {
        "started": True,
        "run_id": run_id,
        "pid": detached["pid"],
        "log": detached["log"],
    }


def prompt(record: RunRecord, repo: Path) -> str:
    previous = (
        f" It last stopped as `{record.phase}`"
        + (f": {record.last_failure}" if record.last_failure else "")
        + "."
        if record.phase not in {"resuming", PARKED_PHASE}
        else ""
    )
    return (
        f"/meow:run Continue native MEOW run {record.id} in native mode; its "
        f"previous session stopped before success.{previous} Do not start a new "
        "run or ask questions. Inspect "
        f'`meow status {record.id} --json --work-dir "{repo}"`, keep '
        f'`--run-id {record.id}`, `--work-dir "{repo}"` and `--active-dir '
        f'"{record.worktree}"` on every helper, and continue from the last '
        "checkpoint (review an interrupted edit against the plan first). This "
        "is auto-continue mode: success is the only end state. Round limits and "
        "stall rules do not stop you; fix review, check, gate and delivery "
        "failures and repeat until `meow native finalize` returns "
        "`complete: true` and the work is delivered."
    )


def _claude_command(
    record: RunRecord, repo: Path, session_id: str | None, mode: str | None
) -> list[str]:
    claude = shutil.which("claude")
    if claude is None:
        raise FileNotFoundError("claude is not on PATH")
    command = [claude, "-p"]
    if session_id:
        command += ["--resume", session_id, "--fork-session"]
    if mode and mode not in _SKIPPED_MODES:
        command += ["--permission-mode", mode]
    return [*command, prompt(record, repo)]


def _stop_marker(store: RunStore, run_id: str) -> Path:
    return store.directory / f"{run_id}.continue-stop"


def request_stop(repo: Path, run_id: str) -> dict:
    """Ask the supervisor to stop (and kill its session) without cancelling."""
    store = RunStore(repo)
    record = store.load(run_id)
    if not continuing(record):
        return {"run_id": run_id, "stopping": False, "reason": "not_continuing"}
    _stop_marker(store, run_id).write_text("stop\n", encoding="utf-8")
    return {"run_id": run_id, "stopping": True}


def _halt(store: RunStore, run_id: str) -> str | None:
    """Why supervision must end now, if it must."""
    if cancel_requested(store, run_id):
        return "cancelled"
    if _stop_marker(store, run_id).is_file():
        return "stopped"
    phase = store.load(run_id).phase
    if phase in SUCCESS_PHASES or phase == "cancelled":
        return phase
    return None


def _claim(store: RunStore, run_id: str, cap: int, mode: str | None) -> bool:
    record = store.load(run_id)
    if record.phase in SUCCESS_PHASES | {"cancelled"}:
        return False
    if driver_alive(record) and record.driver.get("pid") != os.getpid():
        return False
    record.driver = {
        "pid": os.getpid(),
        "start_identity": process_identity(os.getpid()),
        "host": HOST,
    }
    store._write(record)
    store.update_results(
        run_id,
        auto_continue={"cap": cap, "attempts": 0, "permission_mode": mode},
        heartbeat_at=time.time(),
    )
    return True


def _release(store: RunStore, run_id: str, stopped: str) -> None:
    with suppress(RunStateError, OSError):
        _stop_marker(store, run_id).unlink(missing_ok=True)
        record = store.load(run_id)
        if stopped == "cancelled" and record.phase != "cancelled":
            record = store.transition(
                run_id, "cancelled", last_failure="Cancellation requested"
            )
        if record.driver.get("pid") == os.getpid():
            record.driver = {}
            store._write(record)
        settings = record.results.get("auto_continue") or {}
        store.update_results(run_id, auto_continue={**settings, "stopped": stopped})


def _delay(streak: int, backoff: list) -> float:
    if streak <= 0:
        return 0.0
    return min(float(backoff[min(streak, len(backoff)) - 1]), MAX_BACKOFF)


def _wait(store: RunStore, run_id: str, seconds: float, sleep) -> str | None:
    """Wait, keeping the heartbeat fresh; returns `_halt`'s reason early."""
    deadline = time.monotonic() + seconds
    while True:
        store.heartbeat(run_id)
        if reason := _halt(store, run_id):
            return reason
        left = deadline - time.monotonic()
        if left <= 0:
            return None
        sleep(min(_POLL_SECONDS, left))


def _run_child(
    store: RunStore, record: RunRecord, command: list[str], sleep
) -> str | None:
    """Run one session; returns `_halt`'s reason when it killed the session."""
    env = {**os.environ, CONTINUE_ENV: record.id}
    env.pop(SESSION_ENV, None)
    process = subprocess.Popen(
        command,
        cwd=record.repo,
        env=env,
        stdin=subprocess.DEVNULL,
        start_new_session=sys.platform != "win32",
    )
    try:
        while process.poll() is None:
            reason = None
            with suppress(RunStateError, OSError):
                reason = _halt(store, record.id)
            if reason in {"cancelled", "stopped"}:
                kill_process_tree(process.pid)
                process.wait()
                return reason
            sleep(_POLL_SECONDS)
    except BaseException:
        kill_process_tree(process.pid)
        raise
    return None


def _reopen(store: RunStore, record: RunRecord) -> RunRecord:
    """A failed/exhausted run is not finished in this mode: make it active."""
    if record.phase in TERMINAL_PHASES or record.phase == PARKED_PHASE:
        return store.transition(record.id, "resuming", last_failure=record.last_failure)
    return record


def supervise(  # ruff: ignore[too-many-arguments, too-many-statements]
    repo: Path,
    run_id: str,
    *,
    session_id: str | None = None,
    permission_mode: str | None = None,
    cap: int | None = None,
    sleep=time.sleep,
) -> dict:
    """Relaunch Claude sessions on the run until it succeeds (or `cap` launches)."""
    store = RunStore(repo)
    settings = run_config(repo)
    cap = settings["native_continue_cap"] if cap is None else cap
    if not _claim(store, run_id, cap, permission_mode):
        return {"run_id": run_id, "continued": 0, "stopped": "not_claimed"}
    launches, streak, stopped = 0, 1, "cap"
    fingerprint = progress_fingerprint(store.load(run_id))
    try:
        while not (cap and launches >= cap):
            delay = _delay(streak, settings["retry_backoff"])
            if reason := _wait(store, run_id, delay, sleep):
                stopped = reason
                break
            record = _reopen(store, store.load(run_id))
            session = record.results.get("claude_session_id") or session_id
            command = _claude_command(record, repo, session, permission_mode)
            launches += 1
            store.update_results(
                run_id,
                auto_continue={
                    **(record.results.get("auto_continue") or {}),
                    "attempts": launches,
                },
            )
            print(f"[continue] {run_id} launch {launches}", flush=True)
            if reason := _run_child(store, record, command, sleep):
                stopped = reason
                break
            record = store.load(run_id)
            print(f"[continue] session ended; phase {record.phase}", flush=True)
            if reason := _halt(store, run_id):
                stopped = reason
                break
            current = progress_fingerprint(record)
            streak = 1 if current != fingerprint else streak + 1
            fingerprint = current
    finally:
        _release(store, run_id, stopped)
    return {
        "run_id": run_id,
        "continued": launches,
        "stopped": stopped,
        "phase": store.load(run_id).phase,
    }


def read_payload() -> dict:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}
