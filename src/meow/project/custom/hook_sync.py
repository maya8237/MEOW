"""Register hook definitions with Claude Code so they run in any session.

Like custom skills, each scope's hooks are served by its Claude plugin (the
conventional `plugin/` folder): its `hooks/hooks.json` gets one entry per
definition that runs the definition's command the way MEOW would, so it works
without MEOW installed. Entries are marked by `MEOW_HOOK=`; any other entries
in the file are kept. SDK-only definitions stay with MEOW: those limited to
roles, and the `sprint_*` lifecycle events. Claude Code picks the plugin up on
its next launch.
"""

import json
import math
import shlex
from pathlib import Path

from meow.project.custom.definitions import CustomHook
from meow.project.custom.hook_files import SDK_EVENTS
from meow.project.custom.sources import git_ignored, scope_plugin_dir
from meow.project.custom.sync import (
    CHECK,
    SHARED,
    SYNC_SCOPES,
    register_plugin,
    scope_plugin_registered,
)

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


def _update(
    where: tuple[tuple[str, Path, Path], Path, str],
    config: dict,
    register: bool,
    mode: str,
) -> list[str]:
    """Write `config` (and register the plugin when `register`) unless checking."""
    target, path, label = where
    text = _text(config)
    found = []
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        found.append(f"{label}: out of date")
    if register and not scope_plugin_registered(target):
        found.append(f"{label}: not registered with Claude Code")
    if mode != CHECK and found:
        if register:
            register_plugin(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return found


def _scope_problems(
    target: tuple[str, Path, Path], hooks: tuple[CustomHook, ...], mode: str
) -> list[str]:
    """Describe (and unless `CHECK`, fix) what keeps a scope's hooks from running."""
    scope, project_dir, user_dir = target
    chosen = [h for h in hooks if h.source.scope == scope and syncable(h)]
    path = _file(scope, project_dir, user_dir)
    label = f"{scope} hooks ({path})"
    existing = _existing(path)
    if not chosen and not _has_ours(existing):
        return []
    if chosen and scope == "project" and git_ignored(project_dir, path.parent):
        return [f"{label}: skipped, ignored by git so it would not be shared"]
    config = merged_config(existing, chosen)
    if _empty(config):
        return _remove(path, label, mode != CHECK)
    return _update((target, path, label), config, bool(chosen), mode)


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
