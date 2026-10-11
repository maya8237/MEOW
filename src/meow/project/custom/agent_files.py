"""Read one TOML agent definition into validated fields.

The file name is the agent name. Every key is optional except `description`
and `prompt`; unknown keys are rejected so a typo cannot silently drop a
setting.
"""

import tomllib
from pathlib import Path

SESSIONS = ("one-shot", "persistent")
_STRINGS = ("description", "prompt", "model", "ejected_from", "name")
_LISTS = ("tools", "skills", "agents")
_TABLES = ("variants", "fragments")
_KEYS = frozenset({*_STRINGS, *_LISTS, *_TABLES, "session", "lint_on_edit"})


def _text(data: dict, key: str, where: Path) -> str | None:
    value = data.get(key)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"{where}: {key!r} must be non-empty text")
    return value


def _list(data: dict, key: str, where: Path) -> tuple[str, ...] | None:
    if key not in data:
        return None
    value = data[key]
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{where}: {key!r} must be a list of names")
    return tuple(dict.fromkeys(value))


def _table(data: dict, key: str, where: Path) -> dict[str, str]:
    value = data.get(key, {})
    if not isinstance(value, dict) or not all(
        isinstance(text, str) for text in value.values()
    ):
        raise ValueError(f"{where}: [{key}] must map names to text")
    return dict(value)


def _load(path: Path) -> dict:
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
    for key in ("description", "prompt"):
        if not _text(data, key, path):
            raise ValueError(f"{path}: must set {key!r}")
    return data


def _choice(data: dict, path: Path) -> tuple[str, bool]:
    session = data.get("session", "one-shot")
    if session not in SESSIONS:
        raise ValueError(f"{path}: 'session' must be one of {list(SESSIONS)}")
    lint_on_edit = data.get("lint_on_edit", False)
    if not isinstance(lint_on_edit, bool):
        raise ValueError(f"{path}: 'lint_on_edit' must be true or false")
    return session, lint_on_edit


def read_agent_toml(path: Path) -> dict[str, object]:
    """Validated fields of `path`, keyed like `CustomAgent`'s attributes."""
    data = _load(path)
    session, lint_on_edit = _choice(data, path)
    model = _text(data, "model", path)
    return {
        "description": " ".join(data["description"].split()),
        "prompt": data["prompt"],
        "model": None if model in {None, "inherit"} else model,
        "tools": _list(data, "tools", path),
        "skills": _list(data, "skills", path) or (),
        "agents": _list(data, "agents", path) or (),
        "session": session,
        "lint_on_edit": lint_on_edit,
        "ejected_from": _text(data, "ejected_from", path),
        "variants": _table(data, "variants", path),
        "fragments": _table(data, "fragments", path),
    }
