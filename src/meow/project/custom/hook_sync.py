"""Register hook definitions with Claude Code so they run in any session.

Each scope's hooks go into the Claude Code settings file it reads directly and
watches, so they apply without a restart: `.claude/settings.json` (project),
`.claude/settings.local.json` (local), and `~/.claude/settings.json` (user).
One entry per definition runs the definition's command the way MEOW would, so
it works without MEOW installed. Entries are marked by `MEOW_HOOK=`; every
other setting and hook in the file is kept. SDK-only
definitions stay with MEOW: those limited to roles, and the `sprint_*`
lifecycle events. Plugin `hooks.json` files earlier versions wrote are cleaned up.
"""

import json
import math
import shlex
from pathlib import Path

from meow.project.claude_settings import _load_settings, _write_settings
from meow.project.custom.definitions import CustomHook
from meow.project.custom.hook_files import SDK_EVENTS
from meow.project.custom.sources import git_ignored, scope_plugin_dir
from meow.project.custom.sync import CHECK, SHARED, SYNC_SCOPES

_DESCRIPTION = "MEOW hook definitions."
# Every generated command sets this; it marks MEOW's entries in a shared file.
_MARK = "MEOW_HOOK="
# Entries earlier versions generated; replaced on the next sync.
_LEGACY = "meow native hook "
# `user_hooks.SDK_AGENT_ENV`: MEOW's own agents already run their hooks.
SDK_AGENT_ENV = "MEOW_SDK_AGENT"


def syncable(hook: CustomHook) -> bool:
    """Whether Claude Code itself can run `hook` (no MEOW role or lifecycle)."""
    return hook.event in SDK_EVENTS and not (hook.roles or hook.source.roles)


def claude_matcher(matcher: str) -> str:
    """MEOW matches the whole tool name; Claude Code regexes match anywhere."""
    return f"^(?:{matcher})$"


def hook_command(hook: CustomHook) -> str:
    """Shell running `hook` as `meow native hook` does, without MEOW installed.

    Skipped inside MEOW's SDK agents; stdout is discarded so a hook can only
    deny (exit 2, stderr fed back) or inform, never grant a permission.
    """
    return (
        f'[ -n "${{{SDK_AGENT_ENV}:-}}" ] && exit 0; '
        'cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0; '
        f"{_MARK}{shlex.quote(hook.name)} MEOW_EVENT={hook.event} MEOW_ROLE= "
        f"sh -c {shlex.quote(hook.command)} >/dev/null; "
        '[ "$?" -eq 2 ] && exit 2; exit 0'
    )


def hooks_config(hooks: list[CustomHook]) -> dict:
    """The plugin `hooks.json` content for `hooks`."""
    events: dict[str, list[dict]] = {}
    for hook in sorted(hooks, key=lambda item: item.name):
        entry: dict = {
            "hooks": [
                {
                    "type": "command",
                    "command": hook_command(hook),
                    "timeout": math.ceil(hook.timeout),
                }
            ]
        }
        if hook.matcher:
            entry = {"matcher": claude_matcher(hook.matcher), **entry}
        events.setdefault(hook.event, []).append(entry)
    return {"description": _DESCRIPTION, "hooks": events}


def _ours(entry: object) -> bool:
    """Whether a hooks.json entry is one MEOW generated."""
    hooks = entry.get("hooks") if isinstance(entry, dict) else None
    return bool(hooks) and all(
        isinstance(item, dict)
        and (
            _MARK in str(item.get("command", ""))
            or str(item.get("command", "")).startswith(_LEGACY)
        )
        for item in hooks
    )


def _existing(path: Path) -> dict:
    """The current hooks.json, or {}; a file MEOW cannot merge into is refused."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{path} is not valid JSON; it was not changed") from exc
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise ValueError(f"{path} must hold a `hooks` object; it was not changed")
    return data


def merged_config(existing: dict, hooks: list[CustomHook]) -> dict:
    """`existing` with MEOW's entries replaced by those for `hooks`."""
    kept = {
        event: [entry for entry in entries if not _ours(entry)]
        if isinstance(entries, list)
        else entries
        for event, entries in existing.get("hooks", {}).items()
    }
    ours = hooks_config(hooks)["hooks"]
    events = {}
    for event in [*kept, *(name for name in ours if name not in kept)]:
        combined = kept.get(event, [])
        if isinstance(combined, list):
            combined = [*combined, *ours.get(event, [])]
        if combined:
            events[event] = combined
    return {"description": _DESCRIPTION, **existing, "hooks": events}


def _empty(config: dict) -> bool:
    """Nothing but MEOW's own (now empty) content is left."""
    return (
        not config["hooks"]
        and set(config) <= {"description", "hooks"}
        and config.get("description") == _DESCRIPTION
    )


def _text(config: dict) -> str:
    return json.dumps(config, indent=2) + "\n"


def _file(scope: str, project_dir: Path, user_dir: Path) -> Path:
    return scope_plugin_dir(scope, project_dir, user_dir) / "hooks" / "hooks.json"


def _has_ours(existing: dict) -> bool:
    return any(
        _ours(entry)
        for entries in existing.get("hooks", {}).values()
        if isinstance(entries, list)
        for entry in entries
    )


def _remove(path: Path, label: str, apply: bool) -> list[str]:
    if apply:
        path.unlink()
    return [f"{label}: no hook definitions left; removed"]


def _settings_file(scope: str, project_dir: Path) -> Path:
    if scope == "user":
        return Path.home() / ".claude" / "settings.json"
    name = "settings.json" if scope == "project" else "settings.local.json"
    return Path(project_dir) / ".claude" / name


def _settings_problems(
    target: tuple[str, Path, Path], chosen: list[CustomHook], mode: str
) -> list[str]:
    """Project and local hooks live in Claude Code's own settings file.

    A project or local plugin folder in `.claude/skills/` is not loaded, so its
    `hooks.json` never ran. Entries MEOW generated are replaced in place; every
    other setting and hook in the file is kept.
    """
    scope, project_dir, _ = target
    path = _settings_file(scope, project_dir)
    label = f"{scope} hooks ({path})"
    if chosen and scope == "project" and git_ignored(project_dir, path):
        return [f"{label}: skipped, ignored by git so it would not be shared"]
    data = _load_settings(path)
    current = data.get("hooks", {})
    if not isinstance(current, dict):
        raise ValueError(f"{path} must hold a `hooks` object; it was not changed")
    wanted = merged_config({"hooks": current}, chosen)["hooks"]
    found = [] if wanted == current else [f"{label}: out of date"]
    if mode != CHECK and found:
        if wanted:
            data["hooks"] = wanted
        else:
            data.pop("hooks", None)
        if data:
            _write_settings(path, data)
        else:
            path.unlink()
    return found + _old_plugin_hooks(target, mode)


def _old_plugin_hooks(target: tuple[str, Path, Path], mode: str) -> list[str]:
    """Drop MEOW's entries from the plugin hooks file earlier versions wrote."""
    scope, project_dir, user_dir = target
    path = _file(scope, project_dir, user_dir)
    existing = _existing(path)
    if not _has_ours(existing):
        return []
    label = f"{scope} hooks ({path}): old plugin location"
    config = merged_config(existing, [])
    if _empty(config):
        return _remove(path, label, mode != CHECK)
    if mode != CHECK:
        path.write_text(_text(config), encoding="utf-8")
    return [label]


def _scope_problems(
    target: tuple[str, Path, Path], hooks: tuple[CustomHook, ...], mode: str
) -> list[str]:
    """Describe (and unless `CHECK`, fix) what keeps a scope's hooks from running."""
    scope = target[0]
    chosen = [h for h in hooks if h.source.scope == scope and syncable(h)]
    return _settings_problems(target, chosen, mode)


def check_hook_registration(
    hooks: tuple[CustomHook, ...], project_dir: Path, user_dir: Path
) -> list[str]:
    """Problems that keep hooks out of Claude Code; changes nothing."""
    problems = []
    for scope in SYNC_SCOPES:
        try:
            problems += _scope_problems((scope, project_dir, user_dir), hooks, CHECK)
        except (OSError, ValueError) as exc:
            problems.append(f"{scope} hooks: {exc}")
    return problems


def sync_hook_plugins(
    hooks: tuple[CustomHook, ...],
    project_dir: Path,
    user_dir: Path,
    scopes: tuple[str, ...] = SYNC_SCOPES,
) -> list[str]:
    """Write and register every selected scope's hooks; returns what changed."""
    fixed = []
    for scope in scopes:
        fixed += _scope_problems((scope, project_dir, user_dir), hooks, SHARED)
    return fixed
