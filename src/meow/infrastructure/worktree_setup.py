"""Explicit, validated setup for newly created feature worktrees."""

import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path

from meow.infrastructure.checks import run_captured

_SENSITIVE = re.compile(
    r"(^|[._-])(env|secrets?|credentials?|tokens?|keys?)([._-]|$)", re.I
)


def is_secret_like(name: str) -> bool:
    return _SENSITIVE.search(name) is not None


_COMMAND_TIMEOUT = 300


class WorktreeSetupError(RuntimeError):
    """Configured setup failed while retaining the new worktree."""


def _relative(value: object) -> Path:
    if not isinstance(value, str) or not value or value.startswith("-"):
        raise ValueError("worktree setup file paths must be non-empty relative paths")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path == Path("."):
        raise ValueError("worktree setup paths must stay inside the repository")
    if is_secret_like(path.name):
        raise ValueError(f"worktree setup refuses a secret-like file: {value}")
    return path


def validate_setup(raw: object) -> dict[str, list]:
    """Normalize explicit copy paths and literal command argument vectors."""
    if raw is None:
        return {"copy": [], "commands": []}
    if not isinstance(raw, dict) or set(raw) - {"copy", "commands"}:
        raise ValueError("[worktree_setup] accepts only copy and commands")
    copies = raw.get("copy", [])
    commands = raw.get("commands", [])
    if not isinstance(copies, list) or not isinstance(commands, list):
        raise ValueError("[worktree_setup] copy and commands must be arrays")
    normalized_copies = [str(_relative(item)) for item in copies]
    normalized_commands = []
    for command in commands:
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(arg, str) and arg for arg in command)
        ):
            raise ValueError("worktree setup commands must be non-empty string arrays")
        normalized_commands.append(command)
    return {"copy": normalized_copies, "commands": normalized_commands}


def _no_symlinks(root: Path, relative: Path) -> Path:
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise WorktreeSetupError(f"setup path contains a symlink: {relative}")
    if not current.resolve().is_relative_to(root.resolve()):
        raise WorktreeSetupError(f"setup path escapes worktree: {relative}")
    return current


def _already_copied(source: Path, destination: Path) -> bool:
    """True when `destination` is a regular file with exactly `source`'s bytes."""
    if not destination.is_file() or destination.is_symlink():
        return False
    try:
        return source.read_bytes() == destination.read_bytes()
    except OSError:
        return False


def run_setup(  # ruff: ignore[complex-structure, too-many-statements, too-many-arguments, too-many-branches]
    repo: Path,
    worktree: Path,
    manifest: dict[str, list],
    *,
    record_action: Callable[[dict[str, object]], None] | None = None,
    command_decision: Callable[[list[str]], str | None] | None = None,
) -> None:
    """Apply pre-approved setup; report each completed action to the journal."""
    for relative_text in manifest["copy"]:
        relative = _relative(relative_text)
        source = _no_symlinks(repo, relative)
        destination = _no_symlinks(worktree, relative)
        if source.is_file() and _already_copied(source, destination):
            pass  # an earlier attempt copied it; resume re-applies setup
        elif not source.is_file() or destination.exists():
            raise WorktreeSetupError(
                "setup copy requires a regular source and vacant destination: "
                f"{relative}"
            )
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination, follow_symlinks=False)
        if record_action:
            record_action({"kind": "copy", "path": relative_text, "status": "passed"})
    for argv in manifest["commands"]:
        decision = command_decision(argv) if command_decision else None
        if decision in {"deny", "ask"}:
            raise WorktreeSetupError(
                f"setup command requires approval or is denied by policy: {argv[0]}"
            )
        try:
            exit_code, _output, timed_out = run_captured(
                argv, worktree, dict(os.environ), _COMMAND_TIMEOUT
            )
        except (OSError, ValueError) as exc:
            raise WorktreeSetupError(f"setup command failed: {argv[0]}: {exc}") from exc
        if timed_out:
            raise WorktreeSetupError(f"setup command timed out: {argv[0]}")
        if record_action:
            record_action({
                "kind": "command",
                "argv": argv,
                "status": "passed" if exit_code == 0 else "failed",
                "exit_code": exit_code,
            })
        if exit_code:
            raise WorktreeSetupError(
                f"setup command failed: {argv[0]} (exit {exit_code})"
            )
