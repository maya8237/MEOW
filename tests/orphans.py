"""Event-based checks that a killed command left no descendant running.

The command's script records its grandchild's PID; once the operation under
test has returned, that process must already be gone. No test sleeps and then
hopes a marker file stays absent.
"""

import time
from pathlib import Path

from meow.common.process import process_identity

HANG_GUARD_SECONDS = 60


def orphan_script(pidfile: Path) -> str:
    """Python source: spawn a long-lived grandchild, record its PID, then hold."""
    return (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen("
        "[sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )


def grandchild_pid(pidfile: Path) -> int | None:
    """The recorded PID, or None when the command was killed before spawning."""
    try:
        return int(pidfile.read_text())
    except (OSError, ValueError):
        return None


def assert_grandchild_dead(pidfile: Path) -> None:
    pid = grandchild_pid(pidfile)
    if pid is None:
        return  # killed before it spawned anything, so nothing can have leaked
    deadline = time.monotonic() + HANG_GUARD_SECONDS  # bounds a hang only
    while process_identity(pid) is not None:
        assert time.monotonic() < deadline, f"grandchild {pid} is still running"
        time.sleep(0.05)
