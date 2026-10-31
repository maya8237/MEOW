"""Apply explicitly requested onboarding settings to ignored local config."""

import os
import re
import tempfile
import tomllib
from collections.abc import MutableMapping
from pathlib import Path

import tomlkit

from meow.common.atomic import replace_with_retry
from meow.project.config import _replace_mcp, load_config
from meow.project.onboarding import LOCAL_CONFIG_RELPATH, feature_gaps, onboard_project
from meow.project.repos import remember
from meow.project.tester_setup import inferred_tester_lines


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
            if _replace_mcp(key, dict(document[key]), value):
                document[key] = value
            else:
                _merge(document[key], value)
        else:
            document[key] = value


def _switch_connection(document, parts):
    if parts[-2:] == ["mcp", "from"]:
        for superseded in ("command", "args", "env"):
            document.pop(superseded, None)
    elif parts[-2:] == ["mcp", "command"]:
        document.pop("from", None)


def _set(document, key, value):
    parts = key.split(".")
    for part in parts[:-1]:
        if part not in document:
            document[part] = tomlkit.table()
        document = document[part]
        if not isinstance(document, MutableMapping):
            raise ValueError("Setting path conflicts with an existing value")
    _switch_connection(document, parts)
    document[parts[-1]] = value


def _atomic_write(path: Path, content: bytes):
    """Replace local config atomically; temporary secret files are owner-only."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".onboard-")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        replace_with_retry(Path(temporary), path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _validate_reference(mcp):
    if (
        set(mcp) != {"from"}
        or not isinstance(mcp["from"], str)
        or not mcp["from"].strip()
    ):
        raise ValueError("MCP reference requires only a nonempty from name")


def _validate_inline_mcp(mcp, provider):
    if not isinstance(mcp.get("command"), str) or not mcp["command"].strip():
        raise ValueError(f"{provider} requires an MCP command")
    args = mcp.get("args", [])
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise ValueError("MCP args must be strings")
    env = mcp.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(v, str) for v in env.values()):
        raise ValueError("MCP environment values must be strings")


def _validate_integrations(config, requested):
    for provider in requested & {"jira", "gitlab", "github"}:
        integration = config.get(provider, {})
        mcp = integration.get("mcp", {})
        if "mcp" in integration and mcp == {}:
            continue
        if isinstance(mcp, dict) and "from" in mcp:
            _validate_reference(mcp)
            continue
        _validate_inline_mcp(mcp, provider)


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


def _tester_report(root, report, gaps) -> dict:
    """What the tester setup inferred, what could not be read, and if it is ready."""
    try:
        inferred = inferred_tester_lines(load_config(root))
    except (OSError, ValueError):
        inferred = []
    unreadable = [n for n in report.ambiguous if n.startswith("could not read")]
    ready = (
        gaps["tester_browser_launch"]["configured"]
        and gaps["tester_dev_server"]["configured"]
        and not unreadable
    )
    return {"inferred": inferred, "could_not_read": unreadable, "tester_ready": ready}


def configure_project(root, settings, *, config_file=None):
    """Refresh base setup, apply explicit flags, validate, and report no secrets."""
    root = Path(root)
    path = root / LOCAL_CONFIG_RELPATH
    original = path.read_bytes() if path.exists() else None
    settings = tuple(settings)
    _requested_document(original, settings, config_file)
    report = onboard_project(root)
    if report.error:
        raise ValueError(report.error)
    refreshed = path.read_bytes()
    original = refreshed if original is None else original
    document, requested = _requested_document(refreshed, settings, config_file)
    changed = _save_settings(root, document, original, requested)
    remember(root)
    gaps = feature_gaps(root)
    return {
        "files": list(
            dict.fromkeys([*report.files, *([LOCAL_CONFIG_RELPATH] if changed else [])])
        ),
        "configured": [key for key, gap in gaps.items() if gap["configured"]],
        "skipped": [key for key, gap in gaps.items() if not gap["configured"]],
        **_tester_report(root, report, gaps),
        "connection_tested": False,
    }
