"""Bounded, root-only manifest defaults. Never execute or install anything."""

import configparser
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

MAX_MANIFEST_BYTES = 256 * 1024
_LOCKS = {
    "package-lock.json": "npm",
    "npm-shrinkwrap.json": "npm",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
    "bun.lock": "bun",
    "bun.lockb": "bun",
}


def _read(root: Path, name: str) -> str | None:
    path = root / name
    try:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as stream:
            data = stream.read(MAX_MANIFEST_BYTES + 1)
        return data.decode("utf-8") if len(data) <= MAX_MANIFEST_BYTES else None
    except (OSError, UnicodeError):
        return None


@dataclass(frozen=True)
class DetectedCommands:
    defaults: dict
    origins: dict[str, str]
    ambiguous: tuple[str, ...] = ()


def _package(root: Path) -> dict:
    try:
        package = json.loads(_read(root, "package.json") or "{}")
    except (ValueError, RecursionError):
        return {}
    return package if isinstance(package, dict) else {}


def _manager(root: Path, package: dict) -> str | None:
    declared = package.get("packageManager")
    if isinstance(declared, str):
        manager = declared.split("@", 1)[0]
        return manager if manager in {"npm", "pnpm", "yarn", "bun"} else None
    managers = {
        manager
        for lock, manager in _LOCKS.items()
        if (root / lock).is_file() and not (root / lock).is_symlink()
    }
    return next(iter(managers), "npm") if len(managers) <= 1 else None


def _node_checks(root: Path, candidates: dict) -> tuple[str, ...]:
    package = _package(root)
    manager = _manager(root, package)
    scripts = package.get("scripts", {})
    if not isinstance(scripts, dict):
        return ()
    if manager is None:
        return ("package.json: package manager",)
    for script, kind in (("lint", "lint"), ("test", "tests"), ("build", "build")):
        if not isinstance(scripts.get(script), str) or not scripts[script].strip():
            continue
        command = {"command": manager, "args": ["run", script]}
        if kind == "lint":
            command["per_file"] = False
        candidates[kind].append((command, f"package.json:scripts.{script}"))
    return ()


def _python_tool(root: Path) -> dict:
    try:
        project = tomllib.loads(_read(root, "pyproject.toml") or "")
    except (ValueError, RecursionError):
        return {}
    tool = project.get("tool", {})
    return tool if isinstance(tool, dict) else {}


def _ruff_origin(root: Path, tool: dict) -> str | None:
    for name in ("ruff.toml", ".ruff.toml"):
        text = _read(root, name)
        if text is not None:
            try:
                tomllib.loads(text)
            except (ValueError, RecursionError):
                continue
            return name
    return "pyproject.toml:tool.ruff" if isinstance(tool.get("ruff"), dict) else None


def _pytest_origin(root: Path, tool: dict) -> str | None:
    for name, section in (
        ("pytest.ini", "pytest"),
        (".pytest.ini", "pytest"),
        ("setup.cfg", "tool:pytest"),
        ("tox.ini", "pytest"),
    ):
        text = _read(root, name)
        if text is None:
            continue
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read_string(text)
        except configparser.Error:
            continue
        if parser.has_section(section):
            return name
    pytest = tool.get("pytest", {})
    if isinstance(pytest, dict) and isinstance(pytest.get("ini_options"), dict):
        return "pyproject.toml:tool.pytest.ini_options"
    return None


def _python_checks(root: Path, candidates: dict) -> None:
    tool = _python_tool(root)
    ruff_origin = _ruff_origin(root, tool)
    if ruff_origin:
        candidates["lint"].append((
            {
                "command": "python -m ruff check",
                "fix_flag": "--fix",
            },
            ruff_origin,
        ))
    test_origin = _pytest_origin(root, tool)
    if test_origin:
        candidates["tests"].append((
            {"command": "python", "args": ["-m", "pytest"]},
            test_origin,
        ))


def _eslint_origin(root: Path) -> str | None:
    package = _package(root)
    for group in ("dependencies", "devDependencies"):
        dependencies = package.get(group, {})
        if isinstance(dependencies, dict) and "eslint" in dependencies:
            return "package.json:eslint"
    names = (
        "eslint.config.js",
        "eslint.config.mjs",
        "eslint.config.cjs",
        "eslint.config.ts",
        "eslint.config.mts",
        "eslint.config.cts",
        ".eslintrc",
        ".eslintrc.json",
        ".eslintrc.js",
        ".eslintrc.cjs",
        ".eslintrc.yaml",
        ".eslintrc.yml",
    )
    return next((name for name in names if _read(root, name) is not None), None)


def detect_commands(root: Path) -> DetectedCommands:
    """Return conservative check defaults and fixed, credential-free origins."""
    root = Path(root)
    candidates: dict[str, list[tuple[dict, str]]] = {
        "lint": [],
        "tests": [],
        "build": [],
    }
    ambiguous = _node_checks(root, candidates)
    _python_checks(root, candidates)
    eslint = _eslint_origin(root) if not candidates["lint"] else None
    if eslint:
        candidates["lint"].append((
            {
                "command": "npx --no-install eslint",
                "fix_flag": "--fix",
            },
            eslint,
        ))
    defaults, origins = {}, {}
    for kind, choices in candidates.items():
        if choices:
            key = "tester.tests" if kind == "tests" else kind
            origins[key] = ", ".join(origin for _, origin in choices)
            commands = [command for command, _ in choices]
            if kind == "tests":
                defaults["tester"] = {"tests": commands, "auto_test": False}
            else:
                defaults[kind] = commands
    return DetectedCommands(defaults, origins, ambiguous)


def command_defaults(root: Path, explicit: dict) -> DetectedCommands:
    """Supply only missing keys; explicit empty lists and tester opts win."""
    detected = detect_commands(root)
    defaults = {
        key: value for key, value in detected.defaults.items() if key not in explicit
    }
    tester = explicit.get("tester", {})
    if (
        isinstance(tester, dict)
        and "tests" not in tester
        and "tester" in detected.defaults
    ):
        defaults["tester"] = detected.defaults["tester"]
    origins = {
        key: value
        for key, value in detected.origins.items()
        if ("tester" if key == "tester.tests" else key) in defaults
    }
    return DetectedCommands(defaults, origins, detected.ambiguous)


def setup_defaults(root: Path, explicit: dict) -> DetectedCommands:
    """Include lazy MCP references; never copy registrations or credentials."""
    from meow.project.mcp_registry import discover_provider_references

    detected = command_defaults(root, explicit)
    defaults, origins = dict(detected.defaults), dict(detected.origins)
    registry = discover_provider_references(root)
    for provider, reference in registry["references"].items():
        if _provider_missing(explicit, provider):
            defaults[provider] = {"mcp": {"from": reference["from"]}}
            origins[f"{provider}.mcp"] = "Claude MCP registration"
    hints = tuple(
        f"{provider}: {hint}"
        for provider, hint in registry["hints"].items()
        if _provider_missing(explicit, provider)
    )
    return DetectedCommands(defaults, origins, (*detected.ambiguous, *hints))


def _provider_missing(explicit: dict, provider: str) -> bool:
    configured = explicit.get(provider, {})
    return provider not in explicit or (
        isinstance(configured, dict) and "mcp" not in configured
    )
