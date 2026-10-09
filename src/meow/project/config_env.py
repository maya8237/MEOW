"""Environment-variable expansion for values read from config files."""

import os
import re
from pathlib import Path

_CONFIG_ENV_VAR = re.compile(
    r"%([A-Za-z_][A-Za-z0-9_]*)%|\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)"
)


def _config_environment_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value is not None:
        return value
    if name == "USERPROFILE":
        return os.environ.get("HOME") or str(Path.home())
    return None


def _expand_config_environment(value):
    """Expand common environment-variable syntaxes in TOML values."""
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name = match.group(1) or match.group(2) or match.group(3)
            replacement = _config_environment_value(name)
            return replacement if replacement is not None else match.group(0)

        return _CONFIG_ENV_VAR.sub(replace, value)
    if isinstance(value, list):
        return [_expand_config_environment(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_config_environment(item) for key, item in value.items()}
    return value


def unresolved_environment_references(value: object) -> list[str]:
    """Return referenced variables that are unset or empty in the process env."""
    missing: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, str):
            for match in _CONFIG_ENV_VAR.finditer(item):
                name = match.group(1) or match.group(2) or match.group(3)
                if not _config_environment_value(name):
                    missing.add(name)
        elif isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return sorted(missing)
