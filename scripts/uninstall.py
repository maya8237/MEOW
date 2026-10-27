#!/usr/bin/env python3
"""Uninstall the managed MEOW release using only Python's standard library."""

import contextlib
import csv
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

TASK = re.compile(r"meow-([0-9a-f]{32})", re.IGNORECASE)


def path_key(value: str | Path) -> str:
    return os.path.normcase(str(Path(os.path.expandvars(value)).expanduser().resolve()))


def confirmed(prompt: str) -> bool:
    return input(prompt).strip().lower() in {"y", "yes"}


def load_settings(settings: Path) -> dict:
    data = json.loads(settings.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{settings} must contain a JSON object")
    return data


def object_field(data: dict, field: str) -> dict:
    value = data.get(field, {})
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def legacy_path(entry: str, base: Path) -> Path:
    path = Path(os.path.expandvars(entry)).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def owned_marketplace(entry: object, root: Path, base: Path) -> bool:
    """False for a registration that points at someone's own checkout."""
    source = entry.get("source") if isinstance(entry, dict) else None
    path = source.get("path") if isinstance(source, dict) else None
    if not isinstance(path, str):
        return True
    target = legacy_path(path, base)
    return target.is_relative_to(root) or not target.exists()


def clean_plugins(
    data: dict, root: Path, base: Path, names: tuple[str, ...]
) -> list[str]:
    """Drop MEOW's marketplaces and plugins; return names left in place."""
    known = object_field(data, "extraKnownMarketplaces")
    enabled = object_field(data, "enabledPlugins")
    kept = []
    for name in names:
        if name in known and not owned_marketplace(known[name], root, base):
            kept.append(name)
            continue
        known.pop(name, None)
        enabled.pop(f"{name}@{name}", None)
    for field, value in (
        ("extraKnownMarketplaces", known),
        ("enabledPlugins", enabled),
    ):
        if field in data and not value:
            del data[field]
    clean_legacy(data, root, base)
    return kept


def clean_legacy(data: dict, root: Path, base: Path) -> None:
    env = object_field(data, "env")
    legacy = env.get("CLAUDE_CODE_PLUGIN_DIRS")
    if legacy is None:
        return
    if not isinstance(legacy, str):
        raise ValueError("CLAUDE_CODE_PLUGIN_DIRS must be a string")
    kept = [
        entry
        for entry in legacy.split(os.pathsep)
        if entry and not legacy_path(entry, base).is_relative_to(root)
    ]
    if kept:
        env["CLAUDE_CODE_PLUGIN_DIRS"] = os.pathsep.join(kept)
    else:
        del env["CLAUDE_CODE_PLUGIN_DIRS"]
        if not env:
            del data["env"]


def write_settings(settings: Path, data: dict, *, delete_empty: bool = False) -> None:
    if load_settings(settings) == data:
        return
    if delete_empty and not data:
        settings.unlink()
        with contextlib.suppress(OSError):
            settings.parent.rmdir()
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=settings.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        temporary.replace(settings)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def remove_plugin(home: Path, root: Path) -> None:
    settings = home / ".claude/settings.json"
    if settings.exists():
        data = load_settings(settings)
        kept = clean_plugins(data, root, home, ("meow", "meow-user"))
        write_settings(settings, data)
        for name in kept:
            print(f"Kept {name} in {settings}: it points outside {root}.")


def un_onboard(project: Path) -> None:
    project = project.expanduser().resolve()
    root = project / ".meow"
    if project == Path.home().resolve() or not project.is_dir():
        raise ValueError(f"Expected a project directory, not {project}")
    if root.is_symlink() or root.is_junction():
        raise ValueError(f"Refusing linked project directory: {root}")
    updates = []
    for name in ("settings.json", "settings.local.json"):
        settings = project / ".claude" / name
        if settings.exists():
            data = load_settings(settings)
            clean_plugins(data, root, project, ("meow-project", "meow-local"))
            updates.append((settings, data))
    for settings, data in updates:
        write_settings(settings, data, delete_empty=True)
    remove_install(root, root)
    remove_project_ignores(project)
    print(f"Removed MEOW onboarding and project data: {project}")


def remove_project_ignores(project: Path) -> None:
    path = project / ".gitignore"
    if not path.is_file():
        return
    raw = path.read_bytes().decode("utf-8")
    kept = "".join(
        line
        for line in raw.splitlines(True)
        if not line.strip().lstrip("!/").startswith(".meow/")
        and line.strip().lstrip("!/") != ".meow"
    )
    if kept != raw:
        path.write_bytes(kept.encode("utf-8"))


def remove_profiles(home: Path) -> None:  # ruff: ignore[complex-structure]
    line = 'export PATH="$HOME/.meow/bin:$PATH"'
    for name in (".profile", ".bashrc", ".bash_profile", ".zshrc"):
        profile = home / name
        if not profile.is_file():
            continue
        with profile.open(encoding="utf-8", newline="") as stream:
            content = stream.read()
        kept: list[str] = []
        for part in content.splitlines(True):
            if part.strip() != line:
                kept.append(part)
            elif kept and not kept[-1].strip():
                kept.pop()  # the blank line the installer put before it
        text = "".join(kept)
        if text != content:
            with profile.open("w", encoding="utf-8", newline="") as stream:
                stream.write(text)


def remove_path(home: Path) -> None:
    if os.name != "nt":
        remove_profiles(home)
        return
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        "Environment",
        0,
        winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
    ) as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return
        wanted = path_key(home / ".meow/bin")
        kept = ";".join(
            entry
            for entry in current.split(";")
            if not entry or path_key(entry.strip('"')) != wanted
        )
        if kept != current:
            winreg.SetValueEx(key, "Path", 0, kind, kept)
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF, 0x001A, 0, "Environment", 2, 1000, None
    )


def unit_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    if base and Path(base).is_absolute():
        return Path(base) / "systemd" / "user"
    return Path.home() / ".config" / "systemd" / "user"


def find_scheduled() -> list[str]:
    """Ids of MEOW one-shot triggers registered with this user's OS scheduler."""
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["schtasks", "/Query", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        rows = csv.reader(result.stdout.splitlines())
        names = [row[0].lstrip("\\") for row in rows if row]
    else:
        directory = unit_dir()
        names = (
            [path.stem for path in directory.glob("meow-*.timer")]
            if directory.is_dir()
            else []
        )
    return sorted({m[1].lower() for name in names if (m := TASK.fullmatch(name))})


def cancel_scheduled(ids: list[str]) -> None:
    for schedule_id in ids:
        if os.name == "nt":
            result = subprocess.run(
                ["schtasks", "/Delete", "/TN", f"MEOW-{schedule_id}", "/F"],
                capture_output=True,
                text=True,
            )
            if result.returncode:
                raise ValueError(
                    f"Could not delete scheduled task MEOW-{schedule_id}: "
                    f"{(result.stderr or result.stdout).strip()}"
                )
            continue
        name = f"meow-{schedule_id}"
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["systemctl", "--user", "disable", "--now", f"{name}.timer"],
                capture_output=True,
                timeout=30,
            )
        for suffix in ("service", "timer"):
            (unit_dir() / f"{name}.{suffix}").unlink(missing_ok=True)
    if ids and os.name != "nt":
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True,
                timeout=30,
            )


def bun_settings(text: str) -> bool:
    try:
        install = tomllib.loads(text).get("install", {})
    except tomllib.TOMLDecodeError:
        return False
    return (
        isinstance(install, dict)
        and install.get("linker") == "isolated"
        and install.get("globalStore") is True
    )


def bunfig_configured(path: Path) -> bool:
    return path.is_file() and bun_settings(path.read_text(encoding="utf-8"))


def revert_bunfig(path: Path) -> bool:  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
    """Remove the two shared-store keys MEOW's setup writes; True if changed."""
    original = path.read_text(encoding="utf-8")
    if not bun_settings(original):
        return False
    before = tomllib.loads(original)
    in_install, kept = False, []
    key = re.compile(r"^[ \t]*(install\.)?[\"']?(linker|globalStore)[\"']?[ \t]*=")
    header = re.compile(r"^[ \t]*\[(.*?)\]")
    for line in original.splitlines(True):
        if match := header.match(line):
            in_install = match[1].strip().strip("\"'") == "install"
        elif (found := key.match(line)) and (in_install or found[1]):
            continue
        kept.append(line)
    text = "".join(kept)
    expected = {**before, "install": dict(before["install"])}
    del expected["install"]["linker"], expected["install"]["globalStore"]
    if not expected["install"]:
        del expected["install"]
        text = re.sub(
            r"^[ \t]*\[\s*[\"']?install[\"']?\s*\][ \t]*\n?", "", text, flags=re.M
        )
    try:
        valid = tomllib.loads(text) == expected
    except tomllib.TOMLDecodeError:
        valid = False
    if not valid:
        raise ValueError(
            f"Cannot safely edit {path}; remove linker/globalStore by hand"
        )
    if text.strip():
        path.write_text(text, encoding="utf-8")
    else:
        path.unlink()
    return True


def remove_install(path: Path, root: Path) -> None:
    # Never recurse through a symlink or Windows junction into another tree.
    if path.is_symlink():
        path.unlink()
    elif path.is_junction():
        path.rmdir()
    elif path.exists():
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"Refusing to delete outside {root}: {path}")
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def check_python_location(root: Path) -> None:
    executables = {Path(sys.executable).absolute(), Path(sys.executable).resolve()}
    if any(path.is_relative_to(root) for path in executables):
        raise ValueError("Run this script with Python outside ~/.meow")


def uninstall(home: Path, *, purge: bool = False) -> None:
    home = home.resolve()
    root = home / ".meow"
    if root.is_symlink() or root.is_junction():
        raise ValueError(f"Refusing linked install directory: {root}")
    check_python_location(root)
    remove_plugin(home, root)
    remove_path(home)
    targets = (
        [root]
        if purge
        else [root / name for name in ("bin", "versions", "current-version")]
    )
    try:
        for target in targets:
            remove_install(target, root)
    except OSError as exc:
        raise ValueError(
            f"{exc}. Close running MEOW commands and terminals, then rerun."
        ) from exc


def saved_projects(home: Path) -> list[Path]:
    try:
        data = tomllib.loads((home / ".meow/repos.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    repos = data.get("repos", [])
    if not isinstance(repos, list):
        return []
    found = [Path(repo) for repo in repos if isinstance(repo, str)]
    return [
        repo for repo in found if repo.is_dir() and repo.resolve() != home.resolve()
    ]


def ask_removal(home: Path) -> tuple[bool, list[Path], bool]:  # ruff: ignore[complex-structure]
    purge = confirmed("Also delete ALL ~/.meow configuration and run data? [y/N]: ")
    print("Optional: un-onboard projects, deleting their entire .meow directories,")
    print("including configuration, custom definitions, plans, and run history.")
    print("This also removes their MEOW skill/plugin registrations.")
    projects: list[Path] = []
    for project in saved_projects(home):
        if confirmed(f"Un-onboard remembered project {project}? [y/N]: "):
            projects.append(project.resolve())
    while value := input(
        "Other project path to un-onboard (Enter to finish): "
    ).strip():
        project = Path(value.strip("\"'")).expanduser().resolve()
        if not project.is_dir() or project == home.resolve():
            print("Enter an existing project directory.")
            continue
        if project not in projects:
            projects.append(project)
    bun = False
    if bunfig_configured(home / ".bunfig.toml"):
        bun = confirmed(
            "Remove the shared-store linker/globalStore settings MEOW setup may have\n"
            "added to ~/.bunfig.toml? Migrated Bun repos keep their layout. [y/N]: "
        )
    return purge, projects, bun


def main() -> int:  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
    home = Path.home()
    try:
        check_python_location(home.resolve() / ".meow")
        purge, projects, bun = ask_removal(home)
        scheduled = find_scheduled()
        if scheduled:
            print(f"Will cancel {len(scheduled)} scheduled MEOW run(s) in the OS.")
        if not confirmed("Uninstall MEOW now? [y/N]: "):
            print("Cancelled. No files changed.")
            return 0
        failures = 0
        for project in projects:
            print(f"Un-onboarding {project}...", flush=True)
            try:
                un_onboard(project)
            except (OSError, ValueError) as exc:
                failures += 1
                print(f"Could not un-onboard {project}: {exc}", file=sys.stderr)
        if scheduled:
            print("Cancelling scheduled MEOW runs...", flush=True)
        cancel_scheduled(scheduled)
        if bun:
            print("Reverting ~/.bunfig.toml...", flush=True)
            revert_bunfig(home / ".bunfig.toml")
        print("Unregistering the plugin and PATH entry...", flush=True)
        print("Deleting ~/.meow files (can take a while on Windows)...", flush=True)
        uninstall(home, purge=purge)
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return 130
    except (OSError, ValueError) as exc:
        print(f"MEOW uninstall failed: {exc}", file=sys.stderr)
        return 1
    print("MEOW removed. Open a new terminal and restart Claude Code.")
    if not purge:
        print("Kept ~/.meow configuration and run data.")
    if failures:
        print(f"{failures} project(s) could not be un-onboarded.", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
