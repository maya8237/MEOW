"""Reversible Claude Code project hook installation."""

import json
from pathlib import Path

MANIFEST = ".meow/claude-hooks.json"
SETTINGS = ".claude/settings.local.json"
SUPPORTED_EVENTS = {"PostToolUse", "Stop", "SessionEnd"}
COMMANDS = {
    "lint": "meow native hook lint_after_edit",
    "shaping": "meow native hook shaping_ripple",
    "plan_capture": "meow native hook capture_completed_plan",
    "plan_stop": "meow native hook validate_plan_stop",
}


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Claude settings must be a JSON object: {path}")
    return value


def _entry(name: str) -> dict:
    return {
        "matcher": "Write|Edit" if name in {"lint", "shaping"} else "",
        "hooks": [{"type": "command", "command": COMMANDS[name]}],
    }


def _entry_command(entry: dict) -> str | None:
    values = entry.get("hooks")
    if not isinstance(values, list):
        return None
    return next(
        (
            hook.get("command")
            for hook in values
            if isinstance(hook, dict) and isinstance(hook.get("command"), str)
        ),
        None,
    )


def inspect_claude_hooks(  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
    project_dir: Path,
) -> dict[str, str]:
    """Compare MEOW-owned manifest entries with Claude settings without writes."""
    root = Path(project_dir).resolve()
    manifest_path = root / MANIFEST
    result = {name: "missing" for name in COMMANDS}
    if not manifest_path.exists():
        return result
    try:
        manifest = _load(manifest_path)
        entries = manifest["entries"]
        if not isinstance(entries, list):
            raise ValueError("entries must be an array")
        settings = _load(root / SETTINGS)
        hooks = settings.get("hooks", {})
        if not isinstance(hooks, dict):
            raise ValueError("hooks must be an object")
        for item in entries:
            event, entry = item["event"], item["entry"]
            if not isinstance(entry, dict):
                raise ValueError("entry must be an object")
            command = _entry_command(entry)
            name = next(
                (key for key, value in COMMANDS.items() if value == command), None
            )
            if name is None:
                continue
            current = hooks.get(event, [])
            if entry in current:
                result[name] = "active"
            elif any(
                isinstance(value, dict) and _entry_command(value) == command
                for value in current
            ):
                result[name] = "modified"
    except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError):
        result["_diagnostic"] = "malformed manifest"
    return result


def install_claude_hooks(  # ruff: ignore[complex-structure, too-many-statements, too-many-branches]
    project_dir: Path, selected: list[str] | tuple[str, ...], *, dry_run: bool = False
) -> dict:
    root, names = Path(project_dir).resolve(), list(selected)
    unknown = sorted(set(names) - set(COMMANDS))
    if unknown:
        raise ValueError(f"unknown Claude hook(s): {', '.join(unknown)}")
    settings_path, settings = root / SETTINGS, _load(root / SETTINGS)
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("Claude settings 'hooks' must be an object")
    manifest_entries = _load(root / MANIFEST).get("entries", [])
    if not isinstance(manifest_entries, list):
        raise ValueError("Claude hook manifest entries must be an array")
    for name in names:
        event = "PostToolUse" if name in {"lint", "shaping"} else "Stop"
        if event not in SUPPORTED_EVENTS:
            raise ValueError(f"unsupported Claude Code event: {event}")
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            raise ValueError(f"Claude settings hook event {event} must be an array")
        entry = _entry(name)
        if entry not in entries:
            entries.append(entry)
        manifest_item = {"event": event, "entry": entry}
        if manifest_item not in manifest_entries:
            manifest_entries.append(manifest_item)
    result = {
        "settings": str(settings_path),
        "manifest": str(root / MANIFEST),
        "installed": names,
        "dry_run": dry_run,
    }
    if dry_run:
        return result
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    manifest_path = root / MANIFEST
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"version": 1, "entries": manifest_entries}, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def uninstall_claude_hooks(project_dir: Path) -> dict:
    root, manifest_path = (
        Path(project_dir).resolve(),
        Path(project_dir).resolve() / MANIFEST,
    )
    if not manifest_path.exists():
        return {"removed": 0, "idempotent": True}
    manifest, settings_path = _load(manifest_path), root / SETTINGS
    settings, hooks, removed = _load(settings_path), {}, 0
    hooks = settings.get("hooks", {})
    for item in manifest.get("entries", []):
        event, entry = item.get("event"), item.get("entry")
        values = hooks.get(event, []) if isinstance(hooks, dict) else []
        if isinstance(values, list):
            while entry in values:
                values.remove(entry)
                removed += 1
    if settings_path.exists():
        settings_path.write_text(
            json.dumps(settings, indent=2) + "\n", encoding="utf-8"
        )
    manifest_path.unlink(missing_ok=True)
    return {"removed": removed, "idempotent": True}
