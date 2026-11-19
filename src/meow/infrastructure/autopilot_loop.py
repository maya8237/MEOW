"""The autopilot loop: run a command again and again until a success check passes.

The engine is generic. A loop has a command template (Claude by default), an
optional prompt, a success check and an optional cap. Each pass waits with
backoff, launches the command with its output in the loop log, kills it when
it stalls, and checks for success. Only success, a cap or `meow autopilot
stop` end it. A `Backend` adds what a loop drives: native MEOW runs plug in
their own success check, restart rule and session handling.

Loop state lives in `.meow/autopilot/<id>.json`; `<id>.stop` asks the
supervisor to end, `<id>.wake` makes a watching supervisor take over now.
"""

import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path

from meow.common.atomic import replace_with_retry
from meow.common.process import kill_process_tree, process_identity
from meow.execution.queue_state import _pid_is_alive
from meow.infrastructure.background import detach_command

LOOP_ENV = "MEOW_AUTOPILOT_LOOP"
MAX_BACKOFF = 1800.0
POLL_SECONDS = 5.0
DEFAULT_COMMAND = (
    "claude",
    "-p",
    "--output-format",
    "stream-json",
    "--verbose",
    "{resume}",
    "{flags}",
    "{prompt}",
)
_EXPANDING = {"{resume}", "{flags}"}


@dataclass
class LoopState:
    """One autopilot loop, as stored on disk."""

    id: str
    repo: str
    kind: str = "loop"  # "loop": generic; "run": a native MEOW run
    command: list[str] = field(default_factory=lambda: list(DEFAULT_COMMAND))
    prompt: str = ""
    until: str | None = None
    run_id: str | None = None
    cap: int = 0
    stall: float = 1200.0
    answer_wait: float = 600.0
    backoff: list = field(default_factory=lambda: [60, 300, 900])
    flags: list[str] = field(default_factory=list)
    session: str | None = None
    watch_pid: int | None = None
    watch_identity: str | None = None
    watch_session: str | None = None
    transcript: str | None = None
    state: str = "starting"  # watching | waiting | running | stopped
    launches: int = 0
    pid: int | None = None
    identity: str | None = None
    child_pid: int | None = None
    last_exit: int | None = None
    stopped: str | None = None
    log: str = ""
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


def loop_dir(repo: Path) -> Path:
    return Path(repo).resolve() / ".meow" / "autopilot"


def _path(repo: Path, loop_id: str) -> Path:
    if not loop_id or not all(char.isalnum() for char in loop_id):
        raise ValueError(f"Invalid autopilot ID: {loop_id!r}")
    return loop_dir(repo) / f"{loop_id}.json"


def new_id() -> str:
    return secrets.token_hex(6)


def save(state: LoopState) -> LoopState:
    state.updated_at = time.time()
    path = _path(Path(state.repo), state.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(asdict(state), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    replace_with_retry(temporary, path)
    return state


def load(repo: Path, loop_id: str) -> LoopState:
    try:
        data = json.loads(_path(repo, loop_id).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"No autopilot loop {loop_id} in {repo}") from exc
    known = LoopState.__dataclass_fields__
    return LoopState(**{key: value for key, value in data.items() if key in known})


def all_loops(repo: Path) -> list[LoopState]:
    loops = []
    for path in sorted(loop_dir(repo).glob("*.json")):
        with suppress(ValueError):
            loops.append(load(repo, path.stem))
    return loops


def alive(state: LoopState) -> bool:
    """True while this loop's supervisor process runs."""
    if not isinstance(state.pid, int) or state.stopped:
        return False
    if state.identity:
        return process_identity(state.pid) == state.identity
    return _pid_is_alive(state.pid)


def marker(repo: Path, loop_id: str, kind: str) -> Path:
    return _path(repo, loop_id).with_suffix(f".{kind}")


def request_stop(repo: Path, loop_id: str) -> LoopState:
    """End a loop: its supervisor kills the session it launched and exits."""
    state = load(repo, loop_id)
    if alive(state):
        marker(repo, loop_id, "stop").write_text("stop\n", encoding="utf-8")
    elif not state.stopped:
        state.stopped, state.state = "stopped", "stopped"
        save(state)
    return state


def wake(repo: Path, loop_id: str) -> None:
    """Make a watching supervisor take over now (the watched session failed)."""
    marker(repo, loop_id, "wake").write_text("wake\n", encoding="utf-8")


def detach(state: LoopState) -> dict:
    """Start the supervisor for a saved loop in its own process group."""
    repo = Path(state.repo)
    argv = [
        sys.executable,
        "-c",
        "from meow.cli.cli import cli_main; cli_main()",
        "autopilot",
        "_supervise",
        state.id,
        "--work-dir",
        str(repo),
    ]
    log = loop_dir(repo) / f"{state.id}.log"
    if not state.log:
        state.log = str(log)
        save(state)
    return detach_command(repo, argv, str(log), env=dict(os.environ))


def expand(template: list[str], values: dict[str, object]) -> list[str]:
    """Fill a command template. `{resume}` and `{flags}` expand to several
    arguments (or none); an argument that is only `{prompt}` vanishes when
    there is no prompt; other `{name}` fields are replaced in place."""
    argv: list[str] = []
    for part in template:
        if part in _EXPANDING:
            argv.extend(str(item) for item in values.get(part[1:-1]) or [])
            continue
        if part == "{prompt}" and not values.get("prompt"):
            continue
        filled = part
        for key, value in values.items():
            if isinstance(value, (str, int)):
                filled = filled.replace("{" + key + "}", str(value))
        argv.append(filled)
    return argv


class Backend:
    """What a loop drives. The generic backend runs a command until `until`
    (a shell command) exits 0, or until the command itself exits 0."""

    def __init__(self, state: LoopState):
        self.state = state

    def done(self, last_exit: int | None) -> bool:
        if self.state.until:
            try:
                return (
                    subprocess.run(
                        self.state.until, shell=True, cwd=self.state.repo, check=False
                    ).returncode
                    == 0
                )
            except OSError:
                return False
        return last_exit == 0

    def restart_requested(self) -> bool:  # ruff: ignore[no-self-use]
        return False

    def begin(self) -> None:
        """The supervisor started (claim what it drives)."""

    def prepare(self) -> tuple[list[str], dict[str, str], str]:
        """The argv, extra environment and working directory of the next pass."""
        values = {
            "prompt": self.state.prompt,
            "flags": self.state.flags,
            "resume": ["--resume", self.state.session, "--fork-session"]
            if self.state.session
            else [],
            "session": self.state.session or "",
            "repo": self.state.repo,
            "attempt": self.state.launches + 1,
            "loop_id": self.state.id,
        }
        return expand(self.state.command, values), {}, self.state.repo

    def activity(self) -> float:  # ruff: ignore[no-self-use]
        """Newest sign of life besides the loop log (seconds since epoch)."""
        return 0.0

    def progress(self) -> str:  # ruff: ignore[no-self-use]
        return ""

    def tick(self) -> None:
        """Called every poll while waiting (e.g. to keep a heartbeat fresh)."""

    def took_over(self) -> None:
        """The watched session is gone: the loop now launches sessions."""

    def release(self, reason: str) -> None:
        """The loop ended."""


def _mtime(path: str | None) -> float:
    if not path:
        return 0.0
    with suppress(OSError):
        return Path(path).stat().st_mtime
    return 0.0


QUESTION_TOOLS = frozenset({"AskUserQuestion"})
# `Notification` types meaning the session waits for a person at the keyboard.
ASKING_NOTIFICATIONS = frozenset({
    "permission_prompt",
    "idle_prompt",
    "elicitation_dialog",
})


def pending_tools(path: str | None) -> list[str]:  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
    """Names of the tool calls in a transcript or stream-json log that have no
    result yet, oldest first. A running Bash or subagent call is pending too."""
    if not path:
        return []
    try:
        with open(path, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 262144))
            lines = stream.read().decode(errors="replace").splitlines()
    except OSError:
        return []
    pending: dict[str, str] = {}
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("message") if isinstance(entry, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_use" and item.get("id"):
                pending[item["id"]] = str(item.get("name") or "")
            elif item.get("type") == "tool_result":
                pending.pop(item.get("tool_use_id"), None)
    return list(pending.values())


def session_state(path: str | None, asking_since: float, activity: float) -> str:
    """`asking` (a question or permission prompt nobody answered), `busy` (a
    tool call is running: never killed) or `idle`."""
    tools = pending_tools(path)
    if QUESTION_TOOLS & set(tools):
        return "asking"
    if asking_since and asking_since >= activity:
        return "asking"
    if tools:
        return "busy"
    return "idle"


def _since(path: Path) -> float:
    with suppress(OSError):
        return path.stat().st_mtime
    return 0.0


def note_asking(repo: Path, loop_id: str) -> None:
    """The watched session showed a prompt that waits for the user."""
    marker(repo, loop_id, "asking").write_text("asking\n", encoding="utf-8")


class Supervisor:
    """Runs one loop to its end. `sleep` and `now` are injectable for tests."""

    def __init__(
        self,
        state: LoopState,
        backend: Backend,
        *,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
    ):
        self.state, self.backend, self.sleep, self.now = state, backend, sleep, now
        self.repo = Path(state.repo)

    def _halted(self) -> str | None:
        if marker(self.repo, self.state.id, "stop").is_file():
            return "stopped"
        return None

    def _save(self, **changes) -> None:
        for key, value in changes.items():
            setattr(self.state, key, value)
        save(self.state)

    def run(self) -> LoopState:
        existing = load(self.repo, self.state.id)
        if alive(existing) and existing.pid != os.getpid():
            return existing
        self._save(pid=os.getpid(), identity=process_identity(os.getpid()))
        reason = "stopped"
        try:
            self.backend.begin()
            reason = self._watch() or self._loop()
        finally:
            for kind in ("stop", "wake", "asking"):
                marker(self.repo, self.state.id, kind).unlink(missing_ok=True)
            self._save(state="stopped", stopped=reason, child_pid=None)
            self.backend.release(reason)
        return self.state

    # Watching an interactive session the user started ------------------
    def _watched_alive(self) -> bool:
        pid, identity = self.state.watch_pid, self.state.watch_identity
        if not isinstance(pid, int):
            return False
        if identity:
            return process_identity(pid) == identity
        return _pid_is_alive(pid)

    def _watch(self) -> str | None:  # ruff: ignore[complex-structure, too-many-branches]
        """Watch the user's session; return a reason when the loop must end."""
        if not self._watched_alive():
            return None
        self._save(state="watching")
        while True:
            if reason := self._halted():
                return reason
            if self.backend.done(None):
                return "done"
            if marker(self.repo, self.state.id, "wake").is_file():
                break
            if not self._watched_alive():
                break
            if self._should_kill(self.state.transcript):
                kill_process_tree(self.state.watch_pid)
                break
            if self.backend.restart_requested():
                kill_process_tree(self.state.watch_pid)
                break
            self.sleep(POLL_SECONDS)
        for kind in ("wake", "asking"):
            marker(self.repo, self.state.id, kind).unlink(missing_ok=True)
        self.backend.took_over()
        return None

    def _should_kill(self, log: str | None) -> bool:
        """Kill only a session stuck on an unanswered prompt past
        `answer_wait`, or one with no running tool and no activity past
        `stall`. A running tool call is never killed."""
        activity = max(_mtime(log), self.backend.activity())
        asking = _since(marker(self.repo, self.state.id, "asking"))
        state = session_state(log, asking, activity)
        if state == "busy":
            return False
        if state == "asking":
            return self.now() - max(asking, activity) > self.state.answer_wait
        return self.now() - activity > self.state.stall

    # Launching sessions until success -----------------------------------
    def _delay(self, streak: int) -> float:
        if streak <= 0 or not self.state.backoff:
            return 0.0
        index = min(streak, len(self.state.backoff)) - 1
        return min(float(self.state.backoff[index]), MAX_BACKOFF)

    def _wait(self, seconds: float) -> str | None:
        self._save(state="waiting")
        deadline = self.now() + seconds
        while True:
            self.backend.tick()
            if reason := self._halted():
                return reason
            if self.backend.done(None):
                return "done"
            left = deadline - self.now()
            if left <= 0 or self.backend.restart_requested():
                return None
            self.sleep(min(POLL_SECONDS, left))

    def _loop(self) -> str:
        streak, progress = 0, self.backend.progress()
        while True:
            if self.state.cap and self.state.launches >= self.state.cap:
                return "cap"
            if reason := self._wait(self._delay(streak)):
                return reason
            code, reason = self._launch()
            if reason:
                return reason
            if self.backend.done(code):
                return "done"
            current = self.backend.progress()
            streak = 1 if current != progress else streak + 1
            progress = current

    def _launch(self) -> tuple[int | None, str | None]:
        argv, env, cwd = self.backend.prepare()
        self._save(state="running", launches=self.state.launches + 1)
        log = Path(self.state.log or loop_dir(self.repo) / f"{self.state.id}.log")
        print(f"[autopilot] {self.state.id} launch {self.state.launches}", flush=True)
        print(f"[autopilot] command: {json.dumps(argv)}", flush=True)
        if argv and not os.path.isabs(argv[0]) and shutil.which(argv[0]) is None:
            print(f"[autopilot] {argv[0]} is not on PATH", flush=True)
            self._save(last_exit=None)
            return None, None
        child_log = log.with_suffix(f".{self.state.launches}.out")
        with child_log.open("ab") as stream:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env={**os.environ, LOOP_ENV: self.state.id, **env},
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=sys.platform != "win32",
            )
        self._save(child_pid=process.pid)
        try:
            reason = self._monitor(process, child_log)
        except BaseException:
            kill_process_tree(process.pid)
            raise
        self._save(child_pid=None, last_exit=process.returncode)
        print(f"[autopilot] exit {process.returncode}", flush=True)
        return process.returncode, reason

    def _monitor(self, process: subprocess.Popen, child_log: Path) -> str | None:
        while process.poll() is None:
            self.backend.tick()
            if reason := self._halted():
                kill_process_tree(process.pid)
                process.wait()
                return reason
            if self._should_kill(str(child_log)) or self.backend.restart_requested():
                print("[autopilot] session stalled or restarted; killing", flush=True)
                kill_process_tree(process.pid)
                process.wait()
                return None
            self.sleep(POLL_SECONDS)
        return None
