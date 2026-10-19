"""Prepare project-local dependency links using the project's package manager."""

import json
import os
import re
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

from meow.infrastructure.checks import run_captured
from meow.infrastructure.logging import get_logger
from meow.infrastructure.worktree_setup import WorktreeSetupError, _no_symlinks

_LOGGER = get_logger(__name__)

_LOCKS = {
    "bun": ("bun.lock", "bun.lockb"),
    "pnpm": ("pnpm-lock.yaml",),
    "yarn": ("yarn.lock",),
    "npm": ("package-lock.json", "npm-shrinkwrap.json"),
}
_SKIP = {"node_modules", ".git", ".worktrees", ".meow", ".venv", "venv"}


def node_projects(  # ruff: ignore[complex-structure]
    root: Path,
    *,
    include_members: bool = False,
    on_error: Callable[[Path, Exception], None] | None = None,
) -> Iterator[tuple[Path, dict]]:
    """Find install roots, including nested apps; workspace members install together."""
    members: set[Path] = set()
    for directory, dirs, files in root.walk():
        dirs[:] = sorted(
            name for name in dirs if name not in _SKIP and not name.startswith(".")
        )
        if directory.resolve() in members and not include_members:
            dirs.clear()
            continue
        if "package.json" in files:
            try:
                data = _package_json(directory)
                members.update(_workspace_members(directory, data))
            except (OSError, ValueError, WorktreeSetupError) as exc:
                if on_error is None:
                    raise
                on_error(directory, exc)
                continue
            if (directory / "pnpm-workspace.yaml").is_file() and not include_members:
                # pnpm installs the entire workspace from this root, including members
                # declared in its YAML rather than package.json.
                dirs.clear()
            yield directory, data


def _workspace_members(directory: Path, data: dict) -> set[Path]:
    workspaces = data.get("workspaces", [])
    patterns = (
        workspaces.get("packages", []) if isinstance(workspaces, dict) else workspaces
    )
    if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
        raise WorktreeSetupError(f"Invalid workspaces in {directory / 'package.json'}")
    members = set()
    for pattern in patterns:
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise WorktreeSetupError(f"Workspace path escapes project: {pattern}")
        members.update(
            path.resolve() for path in directory.glob(pattern) if path.is_dir()
        )
    return members


def _package_json(directory: Path) -> dict:
    path = directory / "package.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("expected an object")
    except (OSError, ValueError) as exc:
        raise WorktreeSetupError(f"Cannot read {path}: {exc}") from exc
    return data


def package_manager(directory: Path, data: dict) -> tuple[str, str]:
    declared = data.get("packageManager", "")
    if not isinstance(declared, str):
        raise WorktreeSetupError(
            f"Invalid packageManager in {directory / 'package.json'}"
        )
    manager, _, version = declared.partition("@")
    if manager:
        if manager not in _LOCKS:
            raise WorktreeSetupError(
                f"Unsupported package manager {declared!r} in {directory}"
            )
        return manager, version
    managers = [
        name
        for name, locks in _LOCKS.items()
        if any((directory / lock).is_file() for lock in locks)
    ]
    if len(managers) > 1:
        raise WorktreeSetupError(
            f"Multiple package-manager lockfiles in {directory}; "
            "set packageManager in package.json"
        )
    return (managers[0] if managers else "npm"), ""


def install_command(directory: Path, data: dict) -> list[str]:
    manager, version = package_manager(directory, data)
    locked = any((directory / name).is_file() for name in _LOCKS[manager])
    if manager == "npm":
        return [manager, "ci" if locked else "install"]
    argv = [manager, "install"]
    if locked:
        modern_yarn = manager == "yarn" and (
            (version and not version.startswith("1."))
            or (directory / ".yarnrc.yml").is_file()
        )
        argv.append("--immutable" if modern_yarn else "--frozen-lockfile")
    return argv


def run_node_install(  # ruff: ignore[complex-structure, too-many-statements]
    directory: Path,
    argv: list[str],
    *,
    command_decision: Callable[[list[str]], str | None] | None = None,
    record_action: Callable[[dict[str, object]], None] | None = None,
) -> None:
    if command_decision and command_decision(argv) in {"deny", "ask"}:
        raise WorktreeSetupError(
            "Dependency install requires approval or is denied by policy: "
            f"{' '.join(argv)}"
        )
    executable = shutil.which(argv[0])
    if not executable:
        raise WorktreeSetupError(
            f"Missing node_modules in {directory}: {argv[0]} is not on PATH. "
            f"Install it, then rerun MEOW (or run {' '.join(argv)} there)."
        )
    try:
        pending = _no_symlinks(directory, Path(".meow/node-install.pending"))
        pending.parent.mkdir(parents=True, exist_ok=True)
        pending.touch()
        _LOGGER.info("node_dependencies_installing", cwd=str(directory), argv=argv)
        code, output, timed_out = run_captured(
            [executable, *argv[1:]], directory, dict(os.environ), 300
        )
    except (OSError, ValueError) as exc:
        raise WorktreeSetupError(
            f"Dependency install failed in {directory}: {exc}"
        ) from exc
    if record_action:
        record_action({
            "kind": "dependencies",
            "cwd": str(directory),
            "argv": argv,
            "status": "passed" if code == 0 and not timed_out else "failed",
            "exit_code": code,
        })
    if timed_out or code != 0:
        if (
            not timed_out
            and argv[0] == "bun"
            and "--frozen-lockfile" in argv
            and re.search(r"frozen[- ]lockfile|lockfile[^\n]*\bfrozen\b", output, re.I)
        ):
            _LOGGER.warning(
                "bun_frozen_lockfile_retry", cwd=str(directory), output=output
            )
            return run_node_install(
                directory,
                [arg for arg in argv if arg != "--frozen-lockfile"],
                command_decision=command_decision,
                record_action=record_action,
            )
        reason = "timed out" if timed_out else f"exit {code}"
        raise WorktreeSetupError(
            f"Dependency install failed in {directory} ({reason}): "
            f"{' '.join(argv)}\n{output[-4000:]}"
        )
    pending.unlink(missing_ok=True)


def ensure_node_dependencies(
    root: Path,
    *,
    command_decision: Callable[[list[str]], str | None] | None = None,
    record_action: Callable[[dict[str, object]], None] | None = None,
) -> None:
    for directory, data in node_projects(root):
        if (
            not any(
                data.get(key)
                for key in (
                    "dependencies",
                    "devDependencies",
                    "optionalDependencies",
                    "workspaces",
                )
            )
            and not (directory / "pnpm-workspace.yaml").is_file()
        ):
            continue
        pending = directory / ".meow" / "node-install.pending"
        if not pending.exists() and _modules_ready(directory, data):
            continue
        argv = install_command(directory, data)
        run_node_install(
            directory,
            argv,
            command_decision=command_decision,
            record_action=record_action,
        )
        if argv[0] == "bun" and not _modules_ready(directory, data):
            _LOGGER.warning("bun_incomplete_install_retry", cwd=str(directory))
            run_node_install(
                directory,
                [*argv, "--force"],
                command_decision=command_decision,
                record_action=record_action,
            )
            if not _modules_ready(directory, data):
                raise WorktreeSetupError(
                    f"Bun dependencies are still incomplete in {directory} "
                    "after a forced install; inspect Bun's store links and config."
                )


def _modules_ready(root: Path, data: dict) -> bool:
    modules = root / "node_modules"
    if not modules.is_dir() or not any(modules.iterdir()):
        return False
    # Direct packages can survive while a transitive global-store link is broken.
    store = modules / ".bun"
    if store.is_dir() and any(not entry.exists() for entry in store.iterdir()):
        return False
    workspace = data.get("workspaces") or (root / "pnpm-workspace.yaml").is_file()
    projects = (
        node_projects(root, include_members=True) if workspace else [(root, data)]
    )
    return all(
        _declared_dependencies_present(root, directory, package)
        for directory, package in projects
    )


def _declared_dependencies_present(root: Path, directory: Path, data: dict) -> bool:
    required = set(data.get("dependencies", {})) | set(data.get("devDependencies", {}))
    required -= set(data.get("optionalDependencies", {}))
    ancestors = [directory, *directory.parents]
    roots = ancestors[: ancestors.index(root) + 1]
    return all(
        any((parent / "node_modules" / name).is_dir() for parent in roots)
        for name in required
    )
