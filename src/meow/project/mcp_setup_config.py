"""Per-user overrides for how MEOW registers MCP servers: `[mcp_setup.NAME]`.

Only the user config and the ignored local config may change a setup command.
Setup writes into the user's own Claude configuration, so a committed project
config must not be able to choose what gets registered there.
"""

import re
import tomllib
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from meow.project.mcp_setup import _SECRET_MARKERS, SPECS, McpSpec

TABLE = "mcp_setup"
_NAME = re.compile(r"[a-z][a-z0-9_-]*")
_VARIABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_USER_CONFIG = Path(".meow") / "config.toml"
_LOCAL_CONFIG = Path(".meow") / "config.local.toml"


def _bad(name: str, problem: str) -> ValueError:
    # Values are never quoted: they could be credentials.
    return ValueError(f"[{TABLE}.{name}] {problem}")


def _strings(name: str, key: str, value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise _bad(name, f"{key} must be a list of nonempty strings")
    return tuple(value)


def _auth(name: str, key: str, value: object) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list):
        raise _bad(name, f"{key} must be a list of variable-name lists")
    alternatives = tuple(_strings(name, key, item) for item in value)
    if any(not _VARIABLE.fullmatch(var) for alt in alternatives for var in alt):
        raise _bad(name, "auth entries must be environment variable names")
    return alternatives


def _env(name: str, key: str, value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise _bad(name, f"{key} must map variable names to strings")
    for variable in value:
        if not _VARIABLE.fullmatch(variable) or any(
            marker in variable.upper() for marker in _SECRET_MARKERS
        ):
            raise _bad(
                name,
                "env holds fixed, non-secret settings; give secrets through "
                "auth variables in the environment",
            )
    return tuple(value.items())


def _text(name: str, key: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip() or value.startswith("-"):
        raise _bad(name, f"{key} must be a nonempty string")
    return value.strip()


def _warmup(name: str, key: str, value: object) -> tuple[str, ...]:
    return _strings(name, key, value) if value else ()


_FIELDS = {  # key -> (McpSpec field, converter)
    "command": ("launcher", _text),
    "server": ("server", _text),
    "label": ("label", _text),
    "args": ("args", _strings),
    "auth": ("auth", _auth),
    "env": ("env", _env),
    "warmup": ("warmup", _warmup),
}


def _apply(name: str, table: Mapping, current: McpSpec | None) -> McpSpec:
    unknown = sorted(set(table) - set(_FIELDS))
    if unknown:
        raise _bad(name, f"has unknown key(s) {unknown}")
    if current is None and "command" not in table:
        raise _bad(name, "needs a command to define a new provider")
    spec = current or McpSpec(name, name.title(), name, "", (), ())
    changes = {
        field: convert(name, key, table[key])
        for key, (field, convert) in _FIELDS.items()
        if key in table
    }
    return replace(spec, custom=True, **changes)


def _read(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise ValueError(f"cannot read {path}") from None


def _layer(specs: dict[str, McpSpec], path: Path) -> None:
    section = _read(path).get(TABLE, {})
    if not isinstance(section, dict):
        raise ValueError(f"[{TABLE}] in {path} must be a table")
    for name, table in section.items():
        if not _NAME.fullmatch(name) or not isinstance(table, dict):
            raise ValueError(f"[{TABLE}.{name}] needs a lowercase name and a table")
        specs[name] = _apply(name, table, specs.get(name))


def load_specs(
    root: Path | None = None, *, home: Path | None = None
) -> tuple[dict[str, McpSpec], list[str]]:
    """Built-in setup specs with user and local `[mcp_setup.NAME]` overrides.

    Returns the specs and notes about config that was deliberately ignored.
    Raises ValueError, never quoting values, when an override is invalid.
    """
    specs = dict(SPECS)
    notes: list[str] = []
    _layer(specs, (home or Path.home()) / _USER_CONFIG)
    if root is not None:
        _layer(specs, Path(root) / _LOCAL_CONFIG)
        if TABLE in _read(Path(root) / _USER_CONFIG):
            notes.append(
                f"ignored [{TABLE}] in the shared .meow/config.toml: only your "
                "user config or .meow/config.local.toml can change how MCP "
                "servers are registered"
            )
    return specs, notes
