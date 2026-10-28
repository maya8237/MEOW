"""Read one TOML hook definition into validated fields.

The file name is the hook name. `description`, `event`, and `command` are
required; unknown keys are rejected so a typo cannot silently drop a setting.
"""

import re
import tomllib
from pathlib import Path

# Claude SDK / Claude Code events a hook can attach to.
TOOL_EVENTS = ("PreToolUse", "PostToolUse", "PostToolUseFailure")
SDK_EVENTS = (*TOOL_EVENTS, "UserPromptSubmit", "Stop", "SubagentStop")
# MEOW's own events; their hooks are advisory and never stop a run.
LIFECYCLE_EVENTS = ("sprint_start", "sprint_end")
EVENTS = (*SDK_EVENTS, *LIFECYCLE_EVENTS)
DEFAULT_TIMEOUT = 30.0
_KEYS = frozenset({
    "name",
    "description",
    "event",
    "command",
    "matcher",
    "timeout",
    "roles",
})


def _text(data: dict, key: str, path: Path, *, required: bool) -> str | None:
    value = data.get(key)
    if value is None:
        if required:
            raise ValueError(f"{path}: must set {key!r}")
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: {key!r} must be non-empty text")
    return value.strip()


def _matcher(data: dict, event: str, path: Path) -> str | None:
    matcher = _text(data, "matcher", path, required=False)
    if matcher is None:
        return None
    if event not in TOOL_EVENTS:
        raise ValueError(f"{path}: 'matcher' applies only to {list(TOOL_EVENTS)}")
    try:
        re.compile(matcher)
    except re.error as exc:
        raise ValueError(f"{path}: 'matcher' is not a valid regex: {exc}") from exc
    return matcher


def _timeout(data: dict, path: Path) -> float:
    value = data.get("timeout", DEFAULT_TIMEOUT)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ValueError(f"{path}: 'timeout' must be a positive number of seconds")
    return float(value)


def _roles(data: dict, event: str, path: Path) -> tuple[str, ...]:
    if "roles" not in data:
        return ()
    value = data["roles"]
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{path}: 'roles' must be a list of role names")
    if event in LIFECYCLE_EVENTS:
        raise ValueError(f"{path}: {event} hooks run outside any role; drop 'roles'")
    return tuple(dict.fromkeys(value))


def read_hook_toml(path: Path) -> dict[str, object]:
    """Validated fields of `path`, keyed like `CustomHook`'s attributes."""
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path}: {exc}") from exc
    unknown = sorted(set(data) - _KEYS)
    if unknown:
        raise ValueError(f"{path}: unknown key(s) {unknown}")
    if data.get("name", path.stem) != path.stem:
        raise ValueError(f"{path}: name {data['name']!r} must match the file name")
    event = _text(data, "event", path, required=True)
    if event not in EVENTS:
        raise ValueError(f"{path}: 'event' must be one of {list(EVENTS)}")
    return {
        "description": " ".join(
            _text(data, "description", path, required=True).split()
        ),
        "event": event,
        "command": _text(data, "command", path, required=True),
        "matcher": _matcher(data, event, path),
        "timeout": _timeout(data, path),
        "roles": _roles(data, event, path),
    }
