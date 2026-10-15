"""Exited Windows process objects can remain open without being alive."""

import subprocess
import sys

import pytest

from meow.common.process import process_identity
from meow.execution.queue_state import _pid_is_alive


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
