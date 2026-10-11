"""Register MEOW plugin directories in Claude Code's settings files.

Claude Code reads `env.CLAUDE_CODE_PLUGIN_DIRS` from its settings to load
plugins at launch. The installer registers MEOW's own checkout this way, and
the skill creator registers each scope's skill plugin the same way.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

PLUGIN_DIRS_ENV = "CLAUDE_CODE_PLUGIN_DIRS"


def path_key(value: str | Path) -> str:
    return os.path.normcase(str(Path(value).expanduser().resolve(strict=False)))


def _load_settings(settings_path: Path) -> dict[str, object]:
    if not settings_path.is_file():
        return {}
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{settings_path} is not valid JSON; it was not changed"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"{settings_path} must contain a JSON object; it was not changed"
        )
    return data


def _settings_env(data: dict[str, object], settings_path: Path) -> dict[str, object]:
    env = data.get("env")
    if env is None:
        env = {}
    if not isinstance(env, dict):
        raise ValueError(
            f"{settings_path}: the `env` setting must be a JSON object; "
            "it was not changed"
        )
    return env


def _plugin_entries(
    env: dict[str, object], settings_path: Path, separator: str
) -> list[str]:
    existing = env.get(PLUGIN_DIRS_ENV, "")
    if existing is None:
        existing = ""
    if not isinstance(existing, str):
        raise ValueError(
            f"{settings_path}: `env.{PLUGIN_DIRS_ENV}` must be a string; "
            "it was not changed"
        )
    return [entry for entry in existing.split(separator) if entry]


def _write_settings(settings_path: Path, data: dict[str, object]) -> None:
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=settings_path.parent,
            prefix=f".{settings_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
        os.replace(temporary_name, settings_path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _entry_for(plugin_dir: Path, relative_to: Path | None) -> str:
    """The text to store: relative to `relative_to` when it lies beneath it."""
    if relative_to is None:
        return str(plugin_dir)
    try:
        relative = Path(os.path.relpath(plugin_dir, relative_to))
    except ValueError:  # different drive on Windows
        return str(plugin_dir)
    if relative.is_absolute() or ".." in relative.parts:
        return str(plugin_dir)
    return "./" + relative.as_posix()


def _entry_path(entry: str, relative_to: Path | None) -> str:
    path = Path(entry).expanduser()
    if not path.is_absolute() and relative_to is not None:
        path = relative_to / path
    return path_key(path)


def append_plugin_dir(
    settings_path: Path,
    plugin_dir: Path,
    *,
    separator: str | None = None,
    relative_to: Path | None = None,
) -> None:
    """Add `plugin_dir` to a Claude settings file's plugin-dir environment setting.

    With `relative_to` (the project root for a committed or local project
    file), the entry is stored as a relative path so it survives the checkout
    moving. Existing entries are kept, and an entry already present is not
    added twice.
    """
    settings_path = Path(settings_path)
    plugin_dir = Path(plugin_dir).expanduser().resolve()
    base = Path(relative_to).expanduser().resolve() if relative_to else None
    separator = separator or os.pathsep
    data = _load_settings(settings_path)
    env = _settings_env(data, settings_path)
    entries = _plugin_entries(env, settings_path, separator)
    if path_key(plugin_dir) in {_entry_path(entry, base) for entry in entries}:
        return

    entries.append(_entry_for(plugin_dir, base))
    env[PLUGIN_DIRS_ENV] = separator.join(entries)
    data["env"] = env
    _write_settings(settings_path, data)
