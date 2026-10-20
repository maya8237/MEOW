#!/usr/bin/env python3
"""Uninstall the managed MEOW release using only Python's standard library."""

import ctypes
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


def path_key(value: str | Path) -> str:
    return os.path.normcase(str(Path(os.path.expandvars(value)).expanduser().resolve()))


def load_settings(settings: Path) -> dict:
    data = json.loads(settings.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{settings} must contain a JSON object")
    return data


def clean_plugins(data: dict, root: Path, names: tuple[str, ...]) -> None:
    for field in ("enabledPlugins", "extraKnownMarketplaces"):
        entries = data.get(field, {})
        if not isinstance(entries, dict):
            raise ValueError(f"{field} must be an object")
        for name in names:
            entries.pop(f"{name}@{name}" if field == "enabledPlugins" else name, None)
    clean_legacy(data, root)


def clean_legacy(data: dict, root: Path) -> None:
    env = data.get("env", {})
    if not isinstance(env, dict):
        raise ValueError("env must be an object")
    legacy = env.get("CLAUDE_CODE_PLUGIN_DIRS")
    if legacy is not None:
        if not isinstance(legacy, str):
            raise ValueError("CLAUDE_CODE_PLUGIN_DIRS must be a string")
        kept = [
            entry
            for entry in legacy.split(os.pathsep)
            if entry and not legacy_path(entry, root).is_relative_to(root)
        ]
        if kept:
            env["CLAUDE_CODE_PLUGIN_DIRS"] = os.pathsep.join(kept)
        else:
            env.pop("CLAUDE_CODE_PLUGIN_DIRS", None)


def legacy_path(entry: str, root: Path) -> Path:
    path = Path(os.path.expandvars(entry)).expanduser()
    return (path if path.is_absolute() else root.parent / path).resolve()


def write_settings(settings: Path, data: dict) -> None:
    if load_settings(settings) == data:
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
        clean_plugins(data, root, ("meow", "meow-user"))
        write_settings(settings, data)


def clean_hooks(data: dict, manifest: dict) -> None:
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be an object")
    entries = manifest.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError("hook manifest entries must be an array")
    for item in entries:
        if not isinstance(item, dict) or "event" not in item or "entry" not in item:
            raise ValueError("Invalid hook manifest entry")
        values = hooks.get(item["event"], [])
        if isinstance(values, list):
            hooks[item["event"]] = [value for value in values if value != item["entry"]]


def un_onboard(project: Path) -> None:
    project = project.expanduser().resolve()
    root = project / ".meow"
    if project == Path.home().resolve() or not project.is_dir():
        raise ValueError(f"Expected a project directory, not {project}")
    if root.is_symlink() or root.is_junction():
        raise ValueError(f"Refusing linked project directory: {root}")
    manifest = root / "claude-hooks.json"
    owned = load_settings(manifest) if manifest.exists() else {}
    updates = []
    for name in ("settings.json", "settings.local.json"):
        settings = project / ".claude" / name
        if settings.exists():
            data = load_settings(settings)
            clean_plugins(data, root, ("meow-project", "meow-local"))
            clean_hooks(data, owned)
            updates.append((settings, data))
    for settings, data in updates:
        write_settings(settings, data)
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


def remove_profiles(home: Path) -> None:
    line = 'export PATH="$HOME/.meow/bin:$PATH"'
    for name in (".profile", ".bashrc", ".bash_profile", ".zshrc"):
        profile = home / name
        if profile.is_file():
            content = profile.read_text(encoding="utf-8")
            kept = "".join(
                part for part in content.splitlines(True) if part.strip() != line
            )
            if kept != content:
                profile.write_text(kept, encoding="utf-8")


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


def uninstall(home: Path, *, purge: bool = False) -> None:
    home = home.resolve()
    root = home / ".meow"
    if root.is_symlink() or root.is_junction():
        raise ValueError(f"Refusing linked install directory: {root}")
    if Path(sys.executable).resolve().is_relative_to(root):
        raise ValueError("Run this script with Python outside ~/.meow")
    remove_plugin(home, root)
    remove_path(home)
    targets = (
        [root]
        if purge
        else [
            root / name
            for name in (
                "bin",
                "versions",
                "current-version",
            )
        ]
    )
    for target in targets:
        remove_install(target, root)


def main() -> int:
    try:
        purge, projects = ask_removal()
        if input("Uninstall MEOW now? [y/N]: ").strip().lower() not in {"y", "yes"}:
            print("Cancelled. No files changed.")
            return 0
        for project in projects:
            un_onboard(project)
        uninstall(Path.home(), purge=purge)
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return 130
    except (OSError, ValueError) as exc:
        print(f"MEOW uninstall failed: {exc}", file=sys.stderr)
        return 1
    print("MEOW removed. Open a new terminal and restart Claude Code.")
    if not purge:
        print("Kept ~/.meow configuration and run data.")
    return 0


def ask_removal() -> tuple[bool, list[Path]]:
    purge = input("Also delete ALL ~/.meow configuration and run data? [y/N]: ")
    print("Optional: un-onboard projects, deleting their entire .meow directories,")
    print("including configuration, custom definitions, plans, and run history.")
    projects = []
    while value := input("Project path to un-onboard (Enter to finish): ").strip():
        project = Path(value.strip("\"'")).expanduser().resolve()
        if not project.is_dir() or project == Path.home().resolve():
            print("Enter an existing project directory.")
            continue
        if project not in projects:
            projects.append(project)
    return purge.strip().lower() in {"y", "yes"}, projects


if __name__ == "__main__":
    raise SystemExit(main())
