"""Fault injection for the end-to-end managed-loop tests.

`install()` runs from a generated ``sitecustomize`` in every Python process the
tests spawn, before ``meow`` is imported. It replaces only the sprint driver
(``meow.execution.sprint_runner._drive_sprint``) with a scripted fake generator
and keeps the release server offline. Everything around it is the real code:
the CLI, the run journal, ``park``/``classify_failure``, reconcile, the
supervisor and ``meow resume``. In-process tests install ``drive_sprint`` with
``monkeypatch`` instead.

The fault plan is ``<MEOW_E2E_DIR>/plan.json``: a list of steps. Each driver
launch (a ``_drive`` or a ``resume``) takes the next step: ``{"phase", "round",
"fault"}``, optionally ``"kill_supervisor"``; ``"repeat"`` on the last step
reuses it for every later launch. This module imports nothing from ``meow`` at
the top, so ``python -m meow`` still installs its own platform guard first.
"""

import importlib.abc
import importlib.util
import inspect
import json
import logging
import os
import platform
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

DIR_ENV = "MEOW_E2E_DIR"
WEDGE_ENV = "MEOW_E2E_WEDGE_WMI"
WEDGE_SECONDS = 20
WMI_BUDGET_SECONDS = 5
KEY = "sk-abc123def456ghi789"
RENAME_FAILURES = 12  # 10 exhaust the transition's retries, 2 more hit the park
FAULT_LOGGER = "e2e_fault"
OVERLOAD = "API Error: 529 overloaded_error"
UNAUTHORIZED = "API Error: 401 invalid x-api-key"
SEQUENCE = "API Error: 400 litellm.BadRequestError: roles must alternate"
PER_KEY = (
    f"API Error: 429 litellm.RateLimitError: Model rate limit exceeded for key {KEY} "
    "model gpt-x, retry after 2 seconds"
)
RESOURCES = "Insufficient system resources exist to complete the requested service"
KILL = getattr(signal, "SIGKILL", signal.SIGTERM)  # Windows has no SIGKILL


def _directory() -> Path:
    return Path(os.environ[DIR_ENV])


def record(event: str, **fields: object) -> None:
    """Append one JSON line to ``events.jsonl`` (read by the test)."""
    line = json.dumps({"event": event, "pid": os.getpid(), **fields})
    with (_directory() / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")


def next_step() -> tuple[int, dict]:
    """Count this driver launch and return it with its scripted step."""
    directory = _directory()
    steps = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    counter = directory / "launches"
    launch = int(counter.read_text(encoding="utf-8")) + 1 if counter.exists() else 1
    counter.write_text(str(launch), encoding="utf-8")
    if launch > len(steps) and not steps[-1].get("repeat"):
        raise AssertionError(f"unexpected driver launch {launch}: no step scripted")
    return launch, steps[min(launch, len(steps)) - 1]


def _log_fault(message: str) -> None:
    """A WARNING+ record: the run's ``.error.log`` keeps it, fsync'd."""
    logging.getLogger(FAULT_LOGGER).error(message)


def _fail_resources(*_args: object) -> None:
    error = OSError(RESOURCES)
    error.winerror = 1450  # what Windows adds to an OSError
    raise error


def _exit_hard(*_args: object) -> None:
    _log_fault("os._exit(137) during generator_started: simulated OOM kill")
    os._exit(137)


def _sigkill(*_args: object) -> None:
    _log_fault("SIGKILL during generator_started: simulated driver kill")
    os.kill(os.getpid(), KILL)
    time.sleep(WEDGE_SECONDS)  # the signal lands before this ends


def _cli_missing(*_args: object) -> None:
    from claude_agent_sdk import CLINotFoundError

    raise CLINotFoundError("Claude Code not found")


def _reset_connection(*_args: object) -> None:
    raise ConnectionResetError(104, "Connection reset by peer")


def _setup_timeout(_store: object, _run_id: str, repo: Path) -> None:
    """`uv sync` times out twice inside the real setup runner."""
    from meow.infrastructure import worktree_setup

    def timed_out(*_args: object, **_kwargs: object) -> tuple[None, str, bool]:
        return None, "", True

    scratch = repo / ".meow" / "e2e-setup"
    scratch.mkdir(parents=True, exist_ok=True)
    manifest = worktree_setup.validate_setup({"commands": [["uv", "sync"]]})
    with patch.object(worktree_setup, "run_captured", timed_out):
        worktree_setup.run_setup(scratch, scratch, manifest)


def _wedged_wmi_start(*_args: object) -> None:
    """The driver started although WMI hangs: platform answers promptly."""
    started = time.monotonic()
    system = platform.system()
    platform.win32_ver()
    elapsed = time.monotonic() - started
    record("wmi_start", system=system, elapsed=elapsed)
    if system != "Windows" or elapsed > WMI_BUDGET_SECONDS:
        raise AssertionError(f"platform guard missing: {system} after {elapsed:.1f}s")
    _reset_connection()


def _assistant(text: str) -> object:
    from claude_agent_sdk import AssistantMessage, TextBlock

    return AssistantMessage(content=[TextBlock(text=text)], model="gpt-x")


def _result(text: str, status: int | None = None) -> object:
    from claude_agent_sdk import ResultMessage

    failed = bool(text)
    return ResultMessage(
        subtype="error_during_execution" if failed else "success",
        duration_ms=0,
        duration_api_ms=0,
        is_error=failed,
        num_turns=1,
        session_id="e2e-generator",
        result=text or "done",
        api_error_status=status,
    )


class _FakeClient:
    """Stands in for ``ClaudeSDKClient``: a scripted stream per turn."""

    def __init__(self, script: Callable[[], list], clients: list) -> None:
        self._script = script
        clients.append(self)

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def query(self, _message: str) -> None:  # ruff: ignore[no-self-use] -- SDK client API
        return None

    async def receive_response(self):
        for item in self._script():
            yield item


async def _generator_turn(repo: Path, script: Callable[[], list]) -> str:
    """One real ``SessionAgent`` turn against a scripted SDK client."""
    from claude_agent_sdk import ClaudeAgentOptions

    from meow.agents import api_errors, base
    from meow.agents.base import ProjectContext, SessionAgent

    async def instant(seconds: float) -> None:  # ruff: ignore[unused-async] -- awaited stub
        record("api_sleep", seconds=seconds)

    clients: list = []
    agent = SessionAgent(ProjectContext(repo, {}))
    agent.role = "generator"
    with (
        patch.object(api_errors, "_sleep", instant),
        patch.object(base, "ClaudeSDKClient", lambda **_: _FakeClient(script, clients)),
    ):
        agent.open(ClaudeAgentOptions(cwd=str(repo), model="gpt-x"))
        try:
            async with agent:
                return await agent.send("Implement the plan")
        finally:
            record("generator_clients", count=len(clients))


def _failing_turn(text: str, status: int) -> Callable:
    async def fault(_store: object, _run_id: str, repo: Path) -> None:
        await _generator_turn(repo, lambda: [_result(text, status)])

    return fault


async def _complete(store: object, run_id: str, repo: Path) -> None:
    await _generator_turn(repo, lambda: [_assistant("done"), _result("")])
    store.transition(run_id, "complete")


def deny_record_renames() -> None:
    """The next renames onto a run record fail like a Defender-locked file.

    Subprocess only: it patches ``os.replace`` for the rest of the process."""
    from meow.common import atomic

    real, left = os.replace, [RENAME_FAILURES]

    def replace(source: object, target: object, *args: object, **kwargs: object):
        if left[0] > 0 and Path(str(target)).parent.name == "runs":
            left[0] -= 1
            denied = PermissionError(13, "Access is denied")
            denied.winerror = 5
            raise denied
        return real(source, target, *args, **kwargs)

    atomic.replace_with_retry.__kwdefaults__.update(delay=0.001, max_delay=0.002)
    os.replace = replace


FAULTS: dict[str, Callable] = {
    "winerror_1450": _fail_resources,
    "exit_137": _exit_hard,
    "sigkill": _sigkill,
    "sequence_400": _failing_turn(SEQUENCE, 400),
    "rename_denied": _reset_connection,  # unreachable: the transition raises first
    "overload_529": _failing_turn(OVERLOAD, 529),
    "setup_timeout": _setup_timeout,
    "per_key_429": _failing_turn(PER_KEY, 429),
    "auth_401": _failing_turn(UNAUTHORIZED, 401),
    "wedged_wmi": _wedged_wmi_start,
    "connection_reset": _reset_connection,
    "complete": _complete,
    "cli_not_found": _cli_missing,
}
ARMS: dict[str, Callable[[], None]] = {"rename_denied": deny_record_renames}


async def drive_sprint(
    working_dir: Path,
    _feature_name: str | None,
    _request: str,
    *,
    run_id: str,
    record_root: Path | None = None,
    **options: object,
) -> None:
    """The scripted stand-in for ``_drive_sprint``."""
    from meow.execution.run_state import RunStore
    from meow.infrastructure.worktree import current_branch

    root = Path(record_root or working_dir)
    store = RunStore(root)
    launch, step = next_step()
    record(
        "launch",
        launch=launch,
        run_id=run_id,
        use_worktree=options.get("use_worktree"),
        **step,
    )
    if step.get("early"):  # fails while preparing: no worktree or branch yet
        store.transition(run_id, "preparing")
    else:
        store.transition(run_id, "preparing", branch=current_branch(working_dir))
        store.set_session(run_id, "generator", "e2e-generator")
        ARMS.get(step["fault"], lambda: None)()
        store.transition(run_id, step["phase"], round=step["round"])
    if step.get("kill_supervisor"):  # the host loses the whole tree
        os.kill(os.getppid(), KILL)
    outcome = FAULTS[step["fault"]](store, run_id, root)
    if inspect.isawaitable(outcome):
        await outcome


class _PatchAfterImport(importlib.abc.MetaPathFinder):
    """Run ``patch(module)`` right after ``name`` first executes."""

    def __init__(self, name: str, patch_module: Callable[[object], None]) -> None:
        self.name = name
        self.patch_module = patch_module

    def find_spec(self, fullname: str, path: object = None, target: object = None):
        if fullname != self.name:
            return None
        sys.meta_path.remove(self)
        spec = importlib.util.find_spec(fullname)
        if spec is None or spec.loader is None:
            return spec
        original, patch_module = spec.loader.exec_module, self.patch_module

        def exec_module(module: object) -> None:
            original(module)
            patch_module(module)

        spec.loader.exec_module = exec_module
        return spec


def _offline(module: object) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("release server disabled in tests")

    module.urlopen = refuse


def _use_fake_driver(module: object) -> None:
    module._drive_sprint = drive_sprint


def _wedge_wmi() -> None:
    """A wedged WMI service, as ``platform`` would see it on Windows."""

    def hang(*_args: object, **_kwargs: object) -> None:
        time.sleep(WEDGE_SECONDS)

    platform._wmi_query = hang
    platform._uname_cache = None


def install() -> None:
    sys.meta_path.insert(0, _PatchAfterImport("meow.installer.maintenance", _offline))
    sys.meta_path.insert(
        0, _PatchAfterImport("meow.execution.sprint_runner", _use_fake_driver)
    )
    if os.environ.get(WEDGE_ENV):
        _wedge_wmi()
