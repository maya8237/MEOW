"""Opt-in Bun shared-store configuration and migration for onboarded projects."""

import re
import sys
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path

from meow.infrastructure.background import detach_command
from meow.infrastructure.node_dependencies import (
    node_projects,
    package_manager,
    run_node_install,
)


def configure_bunfig(path: Path) -> None:
    """Change only the two shared-store settings; preserve other values and comments."""
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    before = tomllib.loads(original)
    install = before.get("install", {})
    if not isinstance(install, dict):
        raise ValueError(f"{path}: install must be a TOML table")
    updated = _install_settings(original)
    expected = {
        **before,
        "install": {**install, "linker": "isolated", "globalStore": True},
    }
    try:
        parsed = tomllib.loads(updated)
    except ValueError as exc:
        raise ValueError(
            f"{path}: cannot safely edit this TOML layout; use an [install] table"
        ) from exc
    if parsed != expected:
        raise ValueError(
            f"{path}: cannot edit shared-store settings without changing other values"
        )
    if updated != original:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(updated, encoding="utf-8")


def shared_store_configured(path: Path) -> bool:
    """True when the user bunfig already enables the isolated shared store."""
    try:
        install = tomllib.loads(path.read_text(encoding="utf-8")).get("install", {})
    except (OSError, ValueError):
        return False
    return (
        isinstance(install, dict)
        and install.get("linker") == "isolated"
        and install.get("globalStore") is True
    )


def _install_settings(text: str) -> str:
    header = re.search(
        r'^\s*\[\s*(?:install|"install"|\'install\')\s*\][^\n]*$', text, re.M
    )
    if header:
        start = header.end()
        next_header = re.search(r"^\s*\[", text[start:], re.M)
        end = start + next_header.start() if next_header else len(text)
        return text[:start] + _store_settings(text[start:end]) + text[end:]
    # Dotted keys define the table too: update them without declaring it twice.
    first_header = re.search(r"^[ \t]*\[", text, re.M)
    end = first_header.start() if first_header else len(text)
    top = text[:end]
    if re.search(r"^[ \t]*install\.", top, re.M):
        return _store_settings(top, prefix="install.") + text[end:]
    return text.rstrip("\n") + '\n[install]\nlinker = "isolated"\nglobalStore = true\n'


def _store_settings(text: str, *, prefix: str = "") -> str:
    for key, value in (("linker", '"isolated"'), ("globalStore", "true")):
        pattern = (
            rf'^([ \t]*{re.escape(prefix)}(?:{key}|"{key}"|\'{key}\')[ \t]*=)[^\n]*'
        )
        if re.search(pattern, text, re.M):
            text = re.sub(
                pattern, lambda match, v=value: match[1] + " " + v, text, flags=re.M
            )
        else:
            text = text.rstrip("\n") + f"\n{prefix}{key} = {value}\n"
    return text


def _warn_project_bunfig(
    paths: Sequence[Path], warned: set[Path], output: Callable[[str], None]
) -> None:
    for config in paths:
        if config.is_file() and config not in warned:
            output(
                f"Warning: {config} may override user Bun settings; "
                "leaving project configuration unchanged."
            )
            warned.add(config)


def _bun_projects(
    projects: Sequence[Path], output: Callable[[str], None]
) -> dict[Path, Path]:
    targets: dict[Path, Path] = {}

    def failed(directory: Path, exc: Exception) -> None:
        output(f"{directory}: Bun discovery failed ({exc}).")

    for project in dict.fromkeys(path.resolve() for path in projects):
        for directory, data in node_projects(
            project, include_members=True, on_error=failed
        ):
            try:
                if package_manager(directory, data)[0] == "bun":
                    targets.setdefault(directory.resolve(), project)
            except (OSError, ValueError, RuntimeError) as exc:
                failed(directory, exc)
    return targets


def migrate_bun_projects(
    projects: Sequence[Path], output: Callable[[str], None]
) -> None:
    targets = _bun_projects(projects, output)
    output(f"Found {len(targets)} Bun dependency folders to migrate.")
    warned: set[Path] = set()
    for directory in targets:
        output(f"{directory}: discovered Bun dependency folder.")
    for directory, project in targets.items():
        try:
            _warn_project_bunfig(
                [project / "bunfig.toml", directory / "bunfig.toml"], warned, output
            )
            argv = ["bun", "install", "--force", "--linker", "isolated"]
            if any((directory / lock).is_file() for lock in ("bun.lock", "bun.lockb")):
                argv.append("--frozen-lockfile")
            output(f"{directory}: rebuilding Bun dependency links for the shared store")
            run_node_install(directory, argv)
            output(f"{directory}: Bun dependency migration completed.")
        except (OSError, ValueError, RuntimeError) as exc:
            output(
                f"{directory}: Bun migration failed ({exc}). "
                "Rerun bun install --force --linker isolated after resolving this."
            )


def start_bun_migration(
    projects: Sequence[Path], output: Callable[[str], None]
) -> dict | None:
    """Keep migration alive after the installer exits; retain all output in a log."""
    if not projects:
        return None
    try:
        job = detach_command(
            working_dir=Path.home(),
            argv=[
                sys.executable,
                "-u",
                "-m",
                "meow.installer",
                "--migrate-bun",
                *map(str, projects),
            ],
        )
    except OSError as exc:
        output(f"Could not start Bun migration: {exc}. Rerun the installer to retry.")
        return None
    output(
        f"Bun repo migration is running in the background (PID {job['pid']}). "
        f"Progress, warnings and failures: {job['log']}. "
        "Installer completion does not mean migration has finished."
    )
    return job
