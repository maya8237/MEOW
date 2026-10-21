"""Prepare project-local dependency links using the project's package manager."""

import json
import os
import re
import shutil
import time
import tomllib
from collections.abc import Callable, Iterator
from functools import partial
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
_ISOLATED = ["--linker", "isolated"]
_DEFAULT_TIMEOUT = 900.0
_BUN_HINT = (
    "Declare every imported package in that folder's package.json; do not switch "
    "the linker to hoisted or edit bunfig.toml to work around it."
)


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
    if manager == "bun" and _project_settings(directory).get("linker") != "hoisted":
        argv += _ISOLATED  # a flag outranks any bunfig, so the shared store stays on
    if locked:
        modern_yarn = manager == "yarn" and (
            (version and not version.startswith("1."))
            or (directory / ".yarnrc.yml").is_file()
        )
        argv.append("--immutable" if modern_yarn else "--frozen-lockfile")
    return argv


def _install_table(path: Path) -> dict:
    try:
        table = tomllib.loads(path.read_text(encoding="utf-8")).get("install")
    except (OSError, ValueError):
        return {}
    return table if isinstance(table, dict) else {}


def _project_settings(directory: Path) -> dict:
    return _install_table(directory / "bunfig.toml")


def bun_settings(directory: Path) -> dict:
    """Effective `[install]` settings: user bunfig overlaid by the project's."""
    return {
        **_install_table(Path.home() / ".bunfig.toml"),
        **_project_settings(directory),
    }


def _install_timeout(configured: float | None = None) -> float:
    if configured:
        return configured
    try:
        return max(float(os.environ.get("MEOW_NODE_INSTALL_TIMEOUT", "")), 1.0)
    except ValueError:
        return _DEFAULT_TIMEOUT


def _warn_bun_config(directory: Path) -> None:
    settings = bun_settings(directory)
    if settings.get("linker") == "hoisted":
        _LOGGER.warning("bun_project_hoisted", cwd=str(directory))
    elif settings.get("globalStore") is False:
        _LOGGER.warning("bun_global_store_disabled", cwd=str(directory))
    if settings.get("production") is True:
        # Bun cannot override this from the command line, so test tooling such as
        # Playwright in devDependencies stays uninstalled until the setting goes.
        _LOGGER.warning("bun_production_skips_dev_dependencies", cwd=str(directory))


def run_node_install(  # ruff: ignore[complex-structure, too-many-statements, too-many-arguments]
    directory: Path,
    argv: list[str],
    *,
    command_decision: Callable[[list[str]], str | None] | None = None,
    record_action: Callable[[dict[str, object]], None] | None = None,
    timeout: float | None = None,
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
        started = time.monotonic()
        code, output, timed_out = run_captured(
            [executable, *argv[1:]],
            directory,
            dict(os.environ),
            _install_timeout(timeout),
        )
        _LOGGER.info(
            "node_dependencies_finished",
            cwd=str(directory),
            seconds=round(time.monotonic() - started, 1),
            exit_code=code,
            timed_out=timed_out,
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
        retry = None if timed_out else _retry_argv(argv, output)
        if retry:
            _LOGGER.warning("node_install_retry", cwd=str(directory), argv=retry)
            return run_node_install(
                directory,
                retry,
                command_decision=command_decision,
                record_action=record_action,
                timeout=timeout,
            )
        reason = "timed out" if timed_out else f"exit {code}"
        if timed_out:
            reason += (
                f" after {_install_timeout(timeout):g}s; raise "
                "[worktree_setup] install_timeout in .meow/config.toml"
            )
        raise WorktreeSetupError(
            f"Dependency install failed in {directory} ({reason}): "
            f"{' '.join(argv)}\n{output[-4000:]}"
        )
    pending.unlink(missing_ok=True)


def _retry_argv(argv: list[str], output: str) -> list[str] | None:
    """Bun-only fallbacks: an old Bun without `--linker`, or a stale lockfile."""
    if argv[0] != "bun":
        return None
    if "--linker" in argv and re.search(
        r"(unknown|unrecognized|invalid)[^\n]*linker", output, re.I
    ):
        return _without_linker(argv)
    if "--frozen-lockfile" in argv and re.search(
        r"frozen[- ]lockfile|lockfile[^\n]*\bfrozen\b", output, re.I
    ):
        return [arg for arg in argv if arg != "--frozen-lockfile"]
    return None


def _without_linker(argv: list[str]) -> list[str]:
    result: list[str] = []
    skip = False
    for arg in argv:
        if skip:
            skip = False
        elif arg == "--linker":
            skip = True
        elif not arg.startswith("--linker="):
            result.append(arg)
    return result


def ensure_node_dependencies(
    root: Path,
    *,
    command_decision: Callable[[list[str]], str | None] | None = None,
    record_action: Callable[[dict[str, object]], None] | None = None,
    timeout: float | None = None,
) -> None:
    install = partial(
        run_node_install,
        command_decision=command_decision,
        record_action=record_action,
        timeout=timeout,
    )
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
        _install_project(directory, data, install)


def _install_project(directory: Path, data: dict, install: Callable[..., None]) -> None:
    pending = directory / ".meow" / "node-install.pending"
    production = _production(directory)
    if not pending.exists() and not _missing(directory, data, production):
        return
    argv = install_command(directory, data)
    if argv[0] == "bun":
        _warn_bun_config(directory)
    install(directory, argv)
    missing = _missing(directory, data, production) if argv[0] == "bun" else []
    if not missing:
        return
    _LOGGER.warning(
        "bun_incomplete_install_retry", cwd=str(directory), missing=missing[:20]
    )
    install(directory, [*argv, "--force"])
    missing = _missing(directory, data, production)
    if missing:
        raise WorktreeSetupError(
            f"Bun dependencies are still incomplete in {directory} after a forced "
            f"install (missing: {', '.join(missing[:10])}). {_BUN_HINT}"
        )


def _production(directory: Path) -> bool:
    """`production` leaves devDependencies out, so they must not count as missing."""
    return bun_settings(directory).get("production") is True


def _missing(root: Path, data: dict, production: bool = False) -> list[str]:
    """Why `node_modules` is not usable; empty means ready."""
    modules = root / "node_modules"
    try:
        if not modules.is_dir() or not any(modules.iterdir()):
            return ["node_modules"]
        # Direct packages can survive while a transitive global-store link is broken.
        store = modules / ".bun"
        if store.is_dir():
            broken = [entry.name for entry in store.iterdir() if not entry.exists()]
            if broken:
                return [f".bun/{name}" for name in sorted(broken)]
        workspace = data.get("workspaces") or (root / "pnpm-workspace.yaml").is_file()
        projects = (
            node_projects(root, include_members=True) if workspace else [(root, data)]
        )
        return [
            name
            for directory, package in projects
            for name in _absent_dependencies(root, directory, package, production)
        ]
    except OSError:
        return ["node_modules"]


def _modules_ready(root: Path, data: dict, production: bool = False) -> bool:
    return not _missing(root, data, production)


def _absent_dependencies(
    root: Path, directory: Path, data: dict, production: bool
) -> list[str]:
    required = set(data.get("dependencies", {}))
    if not production:
        required |= set(data.get("devDependencies", {}))
    required -= set(data.get("optionalDependencies", {}))
    ancestors = [directory, *directory.parents]
    roots = ancestors[: ancestors.index(root) + 1]
    return sorted(
        name
        for name in required
        if not any((parent / "node_modules" / name).is_dir() for parent in roots)
    )
