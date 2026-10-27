"""Apply explicitly requested onboarding settings to ignored local config."""

import os
import re
import tempfile
import tomllib
from collections.abc import MutableMapping
from pathlib import Path

import tomlkit

from meow.project.config import load_config
from meow.project.onboarding import LOCAL_CONFIG_RELPATH, feature_gaps, onboard_project
from meow.project.repos import remember


def parse_setting(raw: str) -> tuple[str, object]:
    """Parse a dotted bare key and a TOML value without returning source errors."""
    key, separator, value = raw.partition("=")
    if not separator or not re.fullmatch(r"[\w-]+(?:\.[\w-]+)*", key, re.ASCII):
        raise ValueError("Expected KEY=TOML_VALUE")
    try:
        parsed = tomllib.loads(f"value = {value}")
    except ValueError:
        raise ValueError("Invalid TOML value") from None
    if set(parsed) != {"value"}:
        raise ValueError("Expected one TOML value")
    return key, parsed["value"]


def _merge(document, values):
    for key, value in values.items():
        if isinstance(value, dict) and isinstance(document.get(key), MutableMapping):
            _merge(document[key], value)
        else:
            document[key] = value


def _set(document, key, value):
    parts = key.split(".")
    for part in parts[:-1]:
        if part not in document:
            document[part] = tomlkit.table()
        document = document[part]
        if not isinstance(document, MutableMapping):
            raise ValueError("Setting path conflicts with an existing value")
    document[parts[-1]] = value


def _atomic_write(path: Path, content: bytes):
    """Replace local config atomically; temporary secret files are owner-only."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".onboard-")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _validate_integrations(config, requested):
    for provider in requested & {"jira", "gitlab", "github"}:
        integration = config.get(provider, {})
        mcp = integration.get("mcp", {})
        if not isinstance(mcp.get("command"), str) or not mcp["command"].strip():
            raise ValueError(f"{provider} requires an MCP command")
        args = mcp.get("args", [])
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise ValueError("MCP args must be strings")
        env = mcp.get("env", {})
        if not isinstance(env, dict) or not all(
            isinstance(v, str) for v in env.values()
        ):
            raise ValueError("MCP environment values must be strings")
        if provider == "jira" and not integration.get("project_key"):
            raise ValueError("Jira requires a project key")


def _save_settings(root, document, original, requested):
    path = root / LOCAL_CONFIG_RELPATH
    content = tomlkit.dumps(document).encode("utf-8")
    changed = content != original
    if changed:
        _atomic_write(path, content)
    try:
        config = load_config(root)
        _validate_integrations(config, requested)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        if changed:
            _atomic_write(path, original)
        raise ValueError("Invalid onboarding configuration") from None
    return changed


def _requested_document(original, settings, config_file):
    document = tomlkit.parse(original.decode("utf-8") if original else "")
    requested = set()
    if config_file is not None:
        values = tomllib.loads(Path(config_file).read_text(encoding="utf-8"))
        _merge(document, values)
        requested.update(values)
    for key, value in settings:
        _set(document, key, value)
        requested.add(key.split(".")[0])
    return document, requested


def configure_project(root, settings, *, config_file=None):
    """Refresh base setup, apply explicit flags, validate, and report no secrets."""
    root = Path(root)
    path = root / LOCAL_CONFIG_RELPATH
    original = path.read_bytes() if path.exists() else None
    document, requested = _requested_document(original, settings, config_file)
    report = onboard_project(root)
    if report.error:
        raise ValueError(report.error)
    original = path.read_bytes() if original is None else original
    if not requested:
        document = tomlkit.parse(original.decode("utf-8"))
    changed = _save_settings(root, document, original, requested)
    remember(root)
    gaps = feature_gaps(root)
    return {
        "files": list(
            dict.fromkeys([*report.files, *([LOCAL_CONFIG_RELPATH] if changed else [])])
        ),
        "configured": [key for key, gap in gaps.items() if gap["configured"]],
        "skipped": [key for key, gap in gaps.items() if not gap["configured"]],
        "connection_tested": False,
    }
