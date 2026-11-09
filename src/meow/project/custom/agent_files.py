"""Read one TOML agent definition into validated fields.

The file name is the agent name. Every key is optional except `description`
and `prompt`; unknown keys are rejected so a typo cannot silently drop a
setting.
"""

import tomllib
from pathlib import Path

SESSIONS = ("one-shot", "persistent")
_STRINGS = ("description", "prompt", "model", "ejected_from", "name")
_LISTS = ("tools", "skills", "agents", "claude_tools")
_TABLES = ("variants", "fragments")
_FLAGS = ("lint_on_edit", "claude_agent")
_KEYS = frozenset({*_STRINGS, *_LISTS, *_TABLES, "session", *_FLAGS})


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


def _flag(data: dict, key: str, path: Path) -> bool:
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise ValueError(f"{path}: {key!r} must be true or false")
    return value


def _choice(data: dict, path: Path) -> tuple[str, bool, bool]:
    session = data.get("session", "one-shot")
    if session not in SESSIONS:
        raise ValueError(f"{path}: 'session' must be one of {list(SESSIONS)}")
    return session, _flag(data, "lint_on_edit", path), _flag(data, "claude_agent", path)


_MD_PREFIX = "meow-"
# Present in every file `agent_sync` generates; such files are never definitions.
GENERATED_KEY = "meow-managed"
_MD_KEYS = {
    "meow-agents": "agents",
    "meow-session": "session",
    "meow-lint-on-edit": "lint_on_edit",
    "meow-claude-agent": "claude_agent",
    "meow-claude-tools": "claude_tools",
    "meow-ejected-from": "ejected_from",
}


def _markdown_values(data: dict, path: Path) -> dict[str, object]:
    """The `meow-*` entries of `data` keyed by field, with text coerced."""
    unknown = sorted(
        key for key in data if key.startswith(_MD_PREFIX) and key not in _MD_KEYS
    )
    if unknown:
        raise ValueError(f"{path}: unknown key(s) {unknown}")
    values = {_MD_KEYS[key]: value for key, value in data.items() if key in _MD_KEYS}
    for flag in _FLAGS:
        text = values.get(flag)
        if isinstance(text, str) and text.lower() in {"true", "false"}:
            values[flag] = text.lower() == "true"
    for key in ("agents", "claude_tools"):
        if isinstance(values.get(key), str):
            values[key] = [part.strip() for part in values[key].split(",")]
    return values


def is_generated(path: Path) -> bool:
    """Whether `path` is a Claude Code copy MEOW generated, not a definition."""
    try:
        head = path.read_bytes()[:4096].decode("utf-8-sig", errors="replace")
    except OSError:
        return False
    return any(line.startswith(f"{GENERATED_KEY}:") for line in head.splitlines())


def read_markdown_settings(data: dict, path: Path) -> dict[str, object]:
    """MEOW-only settings from a Markdown agent's flat `meow-*` frontmatter keys.

    Claude Code ignores these keys. An unknown `meow-*` key is an error so a
    typo cannot silently drop a setting.
    """
    values = _markdown_values(data, path)
    try:
        session, lint_on_edit, claude_agent = _choice(values, path)
        agents = _list(values, "agents", path)
        claude_tools = _list(values, "claude_tools", path)
    except ValueError as exc:
        text = str(exc)
        for key, field in _MD_KEYS.items():
            text = text.replace(f"'{field}'", f"'{key}'")
        raise ValueError(text) from None
    return {
        "agents": agents or (),
        "session": session,
        "lint_on_edit": lint_on_edit,
        "claude_agent": claude_agent,
        "claude_tools": claude_tools,
        "ejected_from": _text(values, "ejected_from", path),
    }


def read_agent_toml(path: Path) -> dict[str, object]:
    """Validated fields of `path`, keyed like `CustomAgent`'s attributes."""
    data = _load(path)
    session, lint_on_edit, claude_agent = _choice(data, path)
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
        "claude_agent": claude_agent,
        "claude_tools": _list(data, "claude_tools", path),
        "ejected_from": _text(data, "ejected_from", path),
        "variants": _table(data, "variants", path),
        "fragments": _table(data, "fragments", path),
    }
