"""Keep child processes from flashing console windows on Windows."""

import os
import subprocess

_PATCHED = "_meow_hides_console"
_VISIBLE_FLAGS = (
    getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    | getattr(subprocess, "DETACHED_PROCESS", 0)
    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
)


def hide_child_consoles() -> None:
    """Start every child (git, linters, servers) with CREATE_NO_WINDOW.

    A console-subsystem program such as git.exe opens its own terminal window
    when launched from a process that has no console (a GUI or background
    host). `subprocess.Popen` backs `subprocess.run` and asyncio subprocesses,
    so one default covers all of them. Explicit console flags are respected.
    No-op off Windows and when already applied.
    """
    if os.name != "nt" or getattr(subprocess.Popen, _PATCHED, False):
        return
    original = subprocess.Popen.__init__

    def init(self, *args, creationflags=0, startupinfo=None, **kwargs):
        if not creationflags & _VISIBLE_FLAGS:
            creationflags |= subprocess.CREATE_NO_WINDOW
            if startupinfo is None:
                # Also ask for a hidden window, for children (such as the
                # Claude CLI's own helpers) that ignore the creation flag.
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
        original(
            self,
            *args,
            creationflags=creationflags,
            startupinfo=startupinfo,
            **kwargs,
        )

    subprocess.Popen.__init__ = init
    setattr(subprocess.Popen, _PATCHED, True)
