"""Command splitting and launch-compatibility policy for configured commands."""

import os
import platform
import shlex
import shutil
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from meow.project.config_files import CONFIG_FILENAME

if TYPE_CHECKING:
    from meow.project.config_models import LintCommand


def split_command(command: str) -> list[str]:
    """Split a configured command consistently for launch and readiness checks."""
    argv = shlex.split(command, posix=os.name != "nt")
    if os.name == "nt":
        argv = [
            value[1:-1]
            if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'"
            else value
            for value in argv
        ]
        if argv:
            argv[0] = argv[0].replace("\\", "/")
    return argv


_WINDOWS_SCRIPT_EXTENSIONS = (".bat", ".cmd")


_WINDOWS_ONLY_PROGRAMS = frozenset({"cmd", "cmd.exe", "powershell", "powershell.exe"})


_UNIX_SCRIPT_EXTENSION = ".sh"


_UNIX_ONLY_PROGRAMS = frozenset({"bash", "sh", "zsh"})


_WSL_LAUNCHERS = frozenset({"wsl", "wsl.exe"})


def _program_name(command: str) -> str:
    """The first token of a lint command, lowercased and path-stripped."""
    first = command.split()[0] if command.split() else ""
    return PurePosixPath(first.replace("\\", "/")).name.lower()


def _os_mismatch(command: str, system: str) -> str | None:
    """None if `command` looks fine to run on `system`; otherwise why not.

    Only catches an explicit, unambiguous marker: a .bat/.cmd/.sh script
    name, or cmd.exe/powershell.exe/bash/sh/zsh invoked directly. `pwsh`
    (PowerShell 7+/Core) is deliberately not included here -- unlike
    `powershell.exe` (Windows PowerShell 5.1), it's genuinely cross-platform,
    so its presence says nothing about which OS the command expects.

    This does NOT check whether the program resolves on PATH at all -- that
    is "not installed yet", a normal, expected condition the lint run itself
    already reports clearly when it happens, not an OS mismatch. There's no
    reliable way to tell "wrong OS" from "not installed yet" for a bare
    program name with none of these markers (ruff, eslint, npx ...,
    golangci-lint, ...), so this check doesn't try -- it only fires on a
    marker that could never be satisfied by installing something on the
    current OS.

    The one deliberate exception to that rule: `bash`/`sh`/`zsh` invoked
    explicitly (e.g. `bash scripts/lint.sh`) are only flagged on Windows if
    none of them resolve on PATH. Git for Windows (an extremely common
    install -- it ships the `git` most Windows developers already have) puts
    a real, working `bash.exe` on PATH, and a project that invokes it
    explicitly this way genuinely runs fine there; checking PATH here isn't
    "is this specific tool installed yet" (project-specific, genuinely
    ambiguous) but "does any POSIX shell interpreter exist on this machine
    at all" (a one-time environment fact, not per-tool). A bare `.sh`
    filename with no explicit interpreter doesn't get this exception, even
    with Git Bash on PATH: Windows has no shebang support, so it can't exec
    a `.sh` file directly the way it execs a `.bat` or `.exe` -- it needs an
    explicit `bash`/`sh` in front of it either way.
    """
    program = _program_name(command)
    if program in _WSL_LAUNCHERS:
        return None  # explicit WSL invocation is fine on native Windows too

    is_unix_only = system == "Windows" and (
        program.endswith(_UNIX_SCRIPT_EXTENSION)
        or (program in _UNIX_ONLY_PROGRAMS and not shutil.which(program))
    )
    is_windows_only = system != "Windows" and (
        program.endswith(_WINDOWS_SCRIPT_EXTENSIONS)
        or program in _WINDOWS_ONLY_PROGRAMS
    )

    if is_unix_only:
        return (
            "is a Unix shell command (a .sh script, or bash/sh/zsh run "
            "directly), which does not run on native Windows. If this "
            "project is meant to run under WSL, invoke it as "
            "`wsl <command>` so meow can tell the difference -- or install "
            "Git for Windows (or another bash/sh/zsh) so the interpreter "
            "itself resolves on PATH."
        )
    if is_windows_only:
        return (
            "is a Windows-only command (a .bat/.cmd script, or "
            "cmd.exe/powershell.exe run directly), which does not run on "
            f"{system}."
        )
    return None


def _validate_os_compatibility(
    commands: "list[LintCommand]", system: str | None = None
) -> None:
    """Fail fast on a lint command that can only ever run on a different OS
    than the one meow is running on right now -- before any agent runs,
    rather than failing obscurely partway through a lint pass."""
    system = system or platform.system()
    for cmd in commands:
        problem = _os_mismatch(" ".join([cmd.command, *cmd.args]), system)
        if problem:
            raise ValueError(
                f"{CONFIG_FILENAME}: lint command {cmd.command!r} {problem} "
                "Fix or remove this [[lint]] entry for this machine."
            )
