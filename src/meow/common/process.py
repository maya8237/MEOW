"""Cross-platform process ownership primitives."""

from __future__ import annotations

import ctypes
import hashlib
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

_WINDOWS_WAIT_TIMEOUT = 0x102


def process_identity(  # ruff: ignore[too-many-return-statements, complex-structure, too-many-branches, too-many-statements]
    pid: int,
) -> str | None:
    """Return an OS process birth identity so PID reuse never counts as live."""
    if sys.platform == "win32":
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = wintypes.HANDLE
        # Query information plus SYNCHRONIZE, so a retained dead object is rejected.
        handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
        if not handle:
            return None
        try:
            if (
                kernel.WaitForSingleObject(wintypes.HANDLE(handle), 0)
                != _WINDOWS_WAIT_TIMEOUT
            ):
                return None
            created = wintypes.FILETIME()
            exited = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            if not kernel.GetProcessTimes(
                wintypes.HANDLE(handle),
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                return None
            return f"{created.dwHighDateTime}:{created.dwLowDateTime}"
        finally:
            kernel.CloseHandle(wintypes.HANDLE(handle))
    stat = Path(f"/proc/{pid}/stat")
    if stat.is_file():
        try:
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
