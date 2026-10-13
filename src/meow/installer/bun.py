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


def migrate_bun_projects(
    projects: Sequence[Path], output: Callable[[str], None]
) -> None:
    for project in projects:
        warned: set[Path] = set()
        try:
            for directory, data in node_projects(project):
                if package_manager(directory, data)[0] == "bun":
                    _warn_project_bunfig(
                        [project / "bunfig.toml", directory / "bunfig.toml"],
                        warned,
                        output,
                    )
                    argv = ["bun", "install", "--force", "--linker", "isolated"]
                    if (directory / "bun.lock").is_file() or (
                        directory / "bun.lockb"
                    ).is_file():
                        argv.append("--frozen-lockfile")
                    output(
                        f"{directory}: rebuilding Bun dependency links "
                        "for the shared store"
                    )
                    run_node_install(directory, argv)
                    output(f"{directory}: Bun dependency migration completed.")
        except (OSError, ValueError, RuntimeError) as exc:
            output(
                f"{project}: Bun migration failed ({exc}). "
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
