"""Autopilot for native MEOW runs: keep a run going until it succeeds.

`/meow:autopilot` drives a native run in the user's Claude session. Its first
checkpoint (`--autopilot`) starts a supervisor that watches that session
(its process, its transcript, and the `Stop`/`StopFailure` hooks). When the
session dies, stalls, or waits on an unanswered question, the supervisor takes
over: it relaunches Claude on the run's latest conversation with the same flags
until the run reaches `complete` or `delivered`. A run that needs the CLI
tester is continued with `meow resume --continue --repair` instead.

`meow cancel ID` on an autopilot run restarts the current session;
`meow autopilot stop ID` is the only thing that ends autopilot.
"""

import os
import sys
import time
from contextlib import suppress
from pathlib import Path

from meow.common.process import process_identity
from meow.execution.run_failures import progress_fingerprint
from meow.execution.run_state import (
    PARKED_PHASE,
    TERMINAL_PHASES,
    RunRecord,
    RunStateError,
    RunStore,
)
from meow.infrastructure import autopilot_loop as loops
from meow.infrastructure.cancellation import cancel_requested, clear_cancel
from meow.infrastructure.claude_flags import merge_flags, process_flags
from meow.project.config import config_root, load_config, run_config

SESSION_ENV = "CLAUDE_CODE_SESSION_ID"
CLAUDE_PID_ENV = "CLAUDE_PID"
HOST = "autopilot"
SUCCESS_PHASES = frozenset({"complete", "delivered"})
DEFAULT_PROMPT = (
    "/meow:autopilot Continue native MEOW run {run_id}; its previous session "
    "stopped before success.{previous} Keep `--run-id {run_id}`, "
    '`--work-dir "{repo}"` and `--active-dir "{worktree}"` on every helper and '
    "continue from the last checkpoint."
)


def _settings(repo: Path) -> dict:
    return run_config(repo)


def loop_of(record: RunRecord) -> loops.LoopState | None:
    """The autopilot loop attached to a run, if any."""
    data = record.results.get("autopilot")
    loop_id = data.get("loop") if isinstance(data, dict) else None
    if not isinstance(loop_id, str):
        return None
    with suppress(ValueError):
        return loops.load(Path(record.repo), loop_id)
    return None


def active(record: RunRecord) -> bool:
    """True while an autopilot supervisor drives or watches this run."""
    state = loop_of(record)
    return state is not None and loops.alive(state)


def may_drive(record: RunRecord) -> bool:
    """This process belongs to a session autopilot runs or watches."""
    state = loop_of(record)
    if state is None or not loops.alive(state):
        return False
    if os.environ.get(loops.LOOP_ENV) == state.id:
        return True
    return state.state in {"starting", "watching"} and bool(
        state.watch_session and os.environ.get(SESSION_ENV) == state.watch_session
    )


def unlimited(record: RunRecord) -> bool:
    """A live autopilot without a cap: parks never end the run."""
    state = loop_of(record)
    return state is not None and loops.alive(state) and not state.cap


def in_autopilot(repo: Path) -> bool:
    """This helper runs inside a session autopilot launched or watches."""
    if os.environ.get(loops.LOOP_ENV):
        return True
    session = os.environ.get(SESSION_ENV)
    return bool(session) and any(
        state.watch_session == session and loops.alive(state)
        for state in loops.all_loops(repo)
    )


def find_transcript(session_id: str | None) -> str | None:
    if not session_id:
        return None
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    for path in (root / "projects").glob(f"*/{session_id}.jsonl"):
        return str(path)
    return None


def run_prompt(record: RunRecord, repo: Path, template: str | None) -> str:
    previous = ""
    if record.phase not in {"resuming", PARKED_PHASE}:
        previous = f" It last stopped as `{record.phase}`" + (
            f": {record.last_failure}." if record.last_failure else "."
        )
    return (template or DEFAULT_PROMPT).format(
        run_id=record.id,
        repo=repo,
        worktree=record.worktree,
        phase=record.phase,
        previous=previous,
    )


def _tester_required(record: RunRecord) -> bool:
    with suppress(Exception):
        tester = load_config(config_root(Path(record.repo), Path(record.worktree)))[
            "tester"
        ]
        return bool(tester.get("tests")) and tester.get("auto_test", True)
    return False


class RunBackend(loops.Backend):  # ruff: ignore[too-many-public-methods]
    """Success is the run reaching `complete` or `delivered`."""

    def __init__(self, state: loops.LoopState):
        super().__init__(state)
        self.repo = Path(state.repo)
        self.store = RunStore(self.repo)
        self.settings = _settings(self.repo)

    def record(self) -> RunRecord | None:
        if not self.state.run_id:  # terminal mode: the first session creates it
            for path in self.store.record_paths():
                with suppress(RunStateError, OSError):
                    record = self.store.load(path.stem)
                    data = record.results.get("autopilot")
                    if isinstance(data, dict) and data.get("loop") == self.state.id:
                        self.state.run_id = record.id
                        loops.save(self.state)
                        break
        if not self.state.run_id:
            return None
        with suppress(RunStateError, OSError):
            return self.store.load(self.state.run_id)
        return None

    def done(self, last_exit: int | None) -> bool:
        record = self.record()
        return record is not None and record.phase in SUCCESS_PHASES

    def restart_requested(self) -> bool:
        """`meow cancel` on an autopilot run: restart, never stop."""
        record = self.record()
        if record is None:
            return False
        if not (cancel_requested(self.store, record.id) or record.phase == "cancelled"):
            return False
        clear_cancel(self.store, record.id)
        if record.phase == "cancelled":
            self.store.transition(record.id, "resuming")
        return True

    def _claim(self) -> None:
        record = self.record()
        if record is None:
            return
        record.driver = {
            "pid": os.getpid(),
            "start_identity": process_identity(os.getpid()),
            "host": HOST,
        }
        self.store._write(record)

    def _drop_driver(self) -> None:
        record = self.record()
        if record is not None and record.driver.get("pid") == os.getpid():
            record.driver = {}
            self.store._write(record)

    def begin(self) -> None:
        self._claim()

    def took_over(self) -> None:
        self._claim()

    def prepare(self) -> tuple[list[str], dict[str, str], str]:
        record = self.record()
        if record is None:  # nothing created yet: start the run again
            return super().prepare()
        if record.phase in TERMINAL_PHASES or record.phase == PARKED_PHASE:
            record = self.store.transition(
                record.id, "resuming", last_failure=record.last_failure
            )
        if _tester_required(record):
            self._drop_driver()  # `meow resume` takes the driver itself
            argv = [
                sys.executable,
                "-c",
                "from meow.cli.cli import cli_main; cli_main()",
                "resume",
                record.id,
                "--continue",
                "--repair",
                "--work-dir",
                str(self.repo),
            ]
            return argv, {}, str(self.repo)
        self._claim()
        self.state.session = record.results.get("claude_session_id") or (
            self.state.session
        )
        prompt = run_prompt(record, self.repo, self.settings.get("autopilot_prompt"))
        values = {
            "prompt": prompt,
            "flags": self.state.flags,
            "resume": ["--resume", self.state.session, "--fork-session"]
            if self.state.session
            else [],
            "session": self.state.session or "",
            "repo": str(self.repo),
            "run_id": record.id,
            "worktree": record.worktree,
            "attempt": self.state.launches + 1,
            "loop_id": self.state.id,
        }
        return loops.expand(self.state.command, values), {}, str(self.repo)

    def activity(self) -> float:
        record = self.record()
        if record is None:
            return 0.0
        beat = record.results.get("heartbeat_at")
        return float(beat) if isinstance(beat, (int, float)) else 0.0

    def progress(self) -> str:
        record = self.record()
        return progress_fingerprint(record) if record is not None else ""

    def tick(self) -> None:
        record = self.record()
        if record is not None and record.driver.get("pid") == os.getpid():
            with suppress(RunStateError, OSError):
                self.store.heartbeat(record.id)

    def release(self, reason: str) -> None:
        self._drop_driver()
        record = self.record()
        if record is not None:
            data = record.results.get("autopilot") or {}
            with suppress(RunStateError, OSError):
                self.store.update_results(
                    record.id, autopilot={**data, "stopped": reason}
                )


def backend_for(state: loops.LoopState) -> loops.Backend:
    return RunBackend(state) if state.kind == "run" else loops.Backend(state)


def new_loop(repo: Path, kind: str, **fields) -> loops.LoopState:
    """A loop with `[run]` defaults; `fields` override them."""
    settings = _settings(repo)
    state = loops.LoopState(
        id=loops.new_id(),
        repo=str(Path(repo).resolve()),
        kind=kind,
        command=list(settings["autopilot_command"]),
        cap=settings["autopilot_cap"],
        stall=float(settings["autopilot_stall"]),
        answer_wait=float(settings["autopilot_answer_wait"]),
        backoff=list(settings["retry_backoff"]),
        flags=list(settings["claude_args"]),
    )
    for key, value in fields.items():
        if value is not None:
            setattr(state, key, value)
    state.log = str(loops.loop_dir(repo) / f"{state.id}.log")
    return loops.save(state)


def attach(repo: Path, record: RunRecord) -> dict:
    """Mark a run as autopilot (checkpoint `--autopilot`) and make sure a
    supervisor watches it. Inside an autopilot-launched session the run joins
    that session's loop instead."""
    store = RunStore(repo)
    current = loop_of(record)
    child_loop = os.environ.get(loops.LOOP_ENV)
    if child_loop and (current is None or current.id != child_loop):
        store.update_results(record.id, autopilot={"loop": child_loop})
        return {"loop": child_loop}
    if current is not None and loops.alive(current):
        return {"loop": current.id}
    session = os.environ.get(SESSION_ENV)
    pid = None
    with suppress(ValueError):
        pid = int(os.environ.get(CLAUDE_PID_ENV, ""))
    settings = _settings(repo)
    state = new_loop(
        repo,
        "run",
        run_id=record.id,
        session=session,
        watch_session=session,
        watch_pid=pid,
        watch_identity=process_identity(pid) if pid else None,
        transcript=find_transcript(session),
        flags=merge_flags(settings["claude_args"], process_flags(pid), None),
    )
    store.update_results(record.id, autopilot={"loop": state.id})
    loops.detach(state)
    return {"loop": state.id}


def _session_loop(
    repo: Path, session_id: str
) -> tuple[RunRecord, loops.LoopState] | None:
    store = RunStore(repo)
    for path in store.record_paths():
        with suppress(RunStateError, OSError):
            record = store.load(path.stem)
            state = loop_of(record)
            if state is not None and session_id in {
                state.watch_session,
                record.results.get("claude_session_id"),
            }:
                return record, state
    return None


def hook(  # ruff: ignore[complex-structure, too-many-branches, too-many-return-statements]
    repo: Path, event: str, payload: dict
) -> dict:
    """`Stop`: keep a watched session from ending before success.
    `Notification`: note a prompt waiting for the user (for `answer_wait`).
    `StopFailure`: hand the run to the background loop now."""
    if os.environ.get(loops.LOOP_ENV):
        return {}  # sessions autopilot launched: the loop relaunches them
    session = payload.get("session_id")
    if not isinstance(session, str) or not (repo / ".meow" / "runs").is_dir():
        return {}
    found = _session_loop(repo, session)
    if found is None:
        return {}
    record, state = found
    if record.phase in SUCCESS_PHASES or state.stopped:
        return {}
    if event == "Stop":
        if not loops.alive(state) or state.state not in {"starting", "watching"}:
            return {}
        return {
            "decision": "block",
            "reason": (
                f"Autopilot: run {record.id} has not succeeded yet. Do not ask the "
                "user anything; make the decision yourself and continue until "
                "`meow native finalize` returns `complete: true`."
            ),
        }
    if event == "Notification":
        if loops.alive(state) and payload.get("notification_type") in (
            loops.ASKING_NOTIFICATIONS
        ):
            loops.note_asking(repo, state.id)
        return {}
    if event == "StopFailure":
        if loops.alive(state):
            loops.wake(repo, state.id)
            return {"woke": state.id}
        settings = _settings(repo)
        fresh = new_loop(
            repo,
            "run",
            run_id=record.id,
            session=session,
            cap=state.cap,
            flags=state.flags
            or merge_flags(settings["claude_args"], [], payload.get("permission_mode")),
        )
        RunStore(repo).update_results(record.id, autopilot={"loop": fresh.id})
        loops.detach(fresh)
        return {"started": fresh.id}
    return {}


def start_run(
    repo: Path, request: str, flags: list[str] | None, cap: int | None
) -> loops.LoopState:
    """Terminal mode: launch Claude on `/meow:autopilot REQUEST` until it succeeds."""
    state = new_loop(
        repo, "run", prompt=f"/meow:autopilot {request}", flags=flags, cap=cap
    )
    loops.detach(state)
    return state


def resume_run(repo: Path, run_id: str, flags: list[str] | None, cap: int | None):
    record = RunStore(repo).load(run_id)
    current = loop_of(record)
    if current is not None and loops.alive(current):
        return current
    state = new_loop(
        repo,
        "run",
        run_id=run_id,
        session=record.results.get("claude_session_id"),
        flags=flags,
        cap=cap,
    )
    RunStore(repo).update_results(run_id, autopilot={"loop": state.id})
    loops.detach(state)
    return state


def start_loop(  # ruff: ignore[too-many-arguments]
    repo: Path,
    prompt: str,
    *,
    until: str | None,
    command: list[str] | None,
    flags: list[str] | None,
    cap: int | None,
) -> loops.LoopState:
    state = new_loop(
        repo, "loop", prompt=prompt, until=until, command=command, flags=flags, cap=cap
    )
    loops.detach(state)
    return state


def resolve(repo: Path, identifier: str) -> loops.LoopState:
    """A loop by its own ID or by the run it drives."""
    with suppress(ValueError):
        return loops.load(repo, identifier)
    record = RunStore(repo).load(identifier)
    state = loop_of(record)
    if state is None:
        raise ValueError(f"Run {identifier} has no autopilot loop.")
    return state


def supervise(repo: Path, loop_id: str) -> loops.LoopState:
    state = loops.load(repo, loop_id)
    return loops.Supervisor(state, backend_for(state)).run()


def stop(repo: Path, identifier: str) -> loops.LoopState:
    state = resolve(repo, identifier)
    loops.request_stop(repo, state.id)
    deadline = time.monotonic() + 15
    while loops.alive(loops.load(repo, state.id)) and time.monotonic() < deadline:
        time.sleep(0.5)
    return loops.load(repo, state.id)
