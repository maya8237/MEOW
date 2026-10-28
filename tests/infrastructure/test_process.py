"""Exited Windows process objects can remain open without being alive."""

import os
import subprocess
import sys
import threading
import time
import types

import pytest

from meow.common.process import process_identity
from meow.execution.queue_state import _pid_is_alive
from meow.infrastructure import test_runner
from meow.infrastructure.checks import run_captured


@pytest.mark.skipif(sys.platform != "win32", reason="Windows retains process handles")
@pytest.mark.parametrize("probe", [process_identity, _pid_is_alive])
def test_exited_process_is_not_live_while_its_handle_is_retained(probe):
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        assert probe(child.pid)
        child.terminate()
        child.wait(timeout=10)
        # Keep Popen and its Windows process handle alive for this check.
        assert not probe(child.pid)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        child.stdin.close()


MAX_SECONDS = 5
SLOW_SECONDS = 10


def test_orphan_holding_output_does_not_wedge(tmp_path):
    started = time.monotonic()
    result = run_captured(
        ["sh", "-c", "sleep 30 & echo done"], tmp_path, dict(os.environ), 60
    )
    assert result == (0, "done\n", False)
    assert time.monotonic() - started < MAX_SECONDS


def test_stdin_is_closed(tmp_path):
    started = time.monotonic()
    code, output, timed_out = run_captured(
        [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"],
        tmp_path,
        dict(os.environ),
        30,
    )
    assert (code, output.replace("\r\n", "\n"), timed_out) == (0, "''\n", False)
    assert time.monotonic() - started < SLOW_SECONDS


def test_timeout_kills_tree_and_keeps_partial_output(tmp_path):
    code, output, timed_out = run_captured(
        ["sh", "-c", "echo partial; sleep 30"], tmp_path, dict(os.environ), 1
    )
    assert (code, timed_out) == (None, True)
    assert output == "partial\n"


def test_cancel_event_interrupts(tmp_path):
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    with pytest.raises(InterruptedError):
        run_captured(
            ["sh", "-c", "sleep 30"],
            tmp_path,
            dict(os.environ),
            60,
            cancel_event=cancel,
        )


def test_windows_uses_job_spawn(tmp_path, monkeypatch):
    seen = {}

    class Process:
        pid = 1
        returncode = 0
        handle = types.SimpleNamespace(Close=lambda: None)

        async def wait(self):  # ruff: ignore[no-self-use] -- stands in for the owned process
            return 0

        def kill(self):
            pass

    def spawn(argv, cwd, env, loop, *, output=None):  # ruff: ignore[too-many-arguments] -- mirrors the real spawner
        seen["argv"] = argv
        output.write(b"hi\n")
        output.flush()
        return Process(), None

    def forbidden(*args, **kwargs):
        raise AssertionError("subprocess.Popen must not be used on nt")

    monkeypatch.setitem(
        sys.modules,
        "pywintypes",
        types.SimpleNamespace(error=type("E", (OSError,), {})),
    )
    monkeypatch.setattr(test_runner, "_spawn_windows_job_process", spawn)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "name", "nt")
    result = run_captured(["cmd", "/c", "echo hi"], tmp_path, {}, 10)
    assert result == (0, "hi\n", False)
    assert seen["argv"] == ["cmd", "/c", "echo hi"]
