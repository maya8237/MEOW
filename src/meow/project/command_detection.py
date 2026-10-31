"""Bounded, root-only manifest defaults. Never execute or install anything."""

import configparser
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

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


def _usable_script(script: object) -> bool:
    """A nonempty script that is not `npm init`'s "no test specified" stub."""
    return (
        isinstance(script, str)
        and bool(script.strip())
        and "no test specified" not in script.lower()
    )


def _node_checks(root: Path, candidates: dict) -> tuple[str, ...]:
    package = _package(root)
    manager = _manager(root, package)
    scripts = package.get("scripts", {})
    if not isinstance(scripts, dict):
        return ()
    if manager is None:
        return ("package.json: package manager",)
    for script, kind in (("lint", "lint"), ("test", "tests"), ("build", "build")):
        if not _usable_script(scripts.get(script)):
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


FRAMEWORK_PORT_FLAGS = {"vite": "--port {port}", "next": "-p {port}"}
_DEV_URL = "http://127.0.0.1:{port}/"
_OWN_BROWSER_CHANNELS = frozenset({"chromium", "chromium-headless-shell"})
_BROWSER_KEYS = frozenset({
    "browser_channel",
    "browser_executable",
    "browser_playwright",
})
_RUN = {"npm": "npm run", "pnpm": "pnpm run", "yarn": "yarn", "bun": "bun run"}


def _package_server(root: Path, notes: list[str]) -> tuple[dict, str] | None:
    """A ``{port}`` dev-server entry for a confirmed framework's dev script."""
    package = _package(root)
    scripts = package.get("scripts")
    scripts = scripts if isinstance(scripts, dict) else {}
    name = next((n for n in ("dev", "start") if _usable_script(scripts.get(n))), None)
    if name is None:
        return None
    deps = {
        **(package.get("dependencies") or {}),
        **(package.get("devDependencies") or {}),
    }
    words = str(scripts[name]).split()
    framework = next(
        (f for f in FRAMEWORK_PORT_FLAGS if f in words),
        next((f for f in FRAMEWORK_PORT_FLAGS if f in deps), None),
    )
    manager = _manager(root, package)
    if framework is None or manager is None:
        notes.append(
            f"package.json scripts.{name}: no confirmed port flag for this framework "
            "or package manager; add [[tester.dev_server]] with {port} yourself"
        )
        return None
    separator = " --" if manager == "npm" else ""
    command = f"{_RUN[manager]} {name}{separator} {FRAMEWORK_PORT_FLAGS[framework]}"
    entry = {"command": command, "ready_url": _DEV_URL, "cwd": "."}
    return entry, f"package.json scripts.{name} ({framework})"


def dev_command_hint(root: Path) -> str | None:
    """``<manager> run dev`` when package.json has a usable dev/start script."""
    package = _package(root)
    scripts = package.get("scripts")
    scripts = scripts if isinstance(scripts, dict) else {}
    manager = _manager(root, package)
    name = next((n for n in ("dev", "start") if _usable_script(scripts.get(n))), None)
    return f"{_RUN[manager]} {name}" if manager and name else None


def _usable_servers(entries: list[dict], origin: str, notes: list[str]) -> list[dict]:
    """Keep only entries the config schema accepts; inference must not break loading."""
    from meow.project.config_schema import _normalize_tester_config

    usable = []
    for entry in entries:
        try:
            _normalize_tester_config({"tester": {"dev_server": [entry]}})
        except ValueError as exc:
            notes.append(f"webServer in {origin} not used: {exc}")
        else:
            usable.append(entry)
    return usable


def _is_absolute(path: str) -> bool:
    return PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute()


def _browser_choices(pw, notes: list[str]) -> set[tuple[str, object]]:
    from meow.project.config_schema import BROWSER_CHANNELS

    chosen: set[tuple[str, object]] = set()
    for channel in pw.channels:
        if channel in BROWSER_CHANNELS:
            chosen.add(("browser_channel", channel))
        elif channel in _OWN_BROWSER_CHANNELS:
            chosen.add(("browser_playwright", True))
        else:
            notes.append(f"channel {channel!r} in {pw.file} is not a supported channel")
    if pw.executable and _is_absolute(pw.executable):
        chosen.add(("browser_executable", pw.executable))
    elif pw.executable:
        notes.append(f"executablePath in {pw.file} is not an absolute path")
    return chosen


def _inferred_browser(pw, notes: list[str]) -> dict:
    """The one browser selection the project's Playwright config makes, if any."""
    chosen = _browser_choices(pw, notes)
    unreadable = any("channel" in i or "executablePath" in i for i in pw.unreadable)
    if not pw.channels and not pw.executable and not unreadable:
        chosen.add(("browser_playwright", True))
    if len(chosen) > 1:
        notes.append(
            f"browser: {pw.file} selects several browsers; set exactly one of "
            "browser_channel, browser_executable or browser_playwright in [tester]"
        )
    return dict(chosen) if len(chosen) == 1 else {}


def _inferred_servers(root: Path, pw, explicit: dict, notes: list[str]):
    """Dev-server entries and their origin, from webServer or package.json."""
    entries = [
        {
            "command": server.command,
            "ready_url": server.url or f"http://127.0.0.1:{server.port}/",
            "cwd": server.cwd,
            **({"startup_timeout": server.timeout} if server.timeout else {}),
        }
        for server in pw.servers
    ]
    if entries:
        return _usable_servers(entries, pw.file or "", notes), pw.file or ""
    if "base_url" in explicit:
        return [], ""
    found = _package_server(root, notes)
    return ([found[0]], found[1]) if found else ([], "")


def _tester_inference(
    root: Path, explicit: dict
) -> tuple[dict, dict[str, str], list[str]]:
    """``[tester]`` defaults read from the project's own test setup; reads only."""
    from meow.project.playwright_config import read_playwright_config

    pw = read_playwright_config(root)
    inferred: dict = {}
    origins: dict[str, str] = {}
    notes = [f"could not read {item}" for item in pw.unreadable]
    servers, origin = (
        ([], "")
        if "dev_server" in explicit
        else _inferred_servers(root, pw, explicit, notes)
    )
    if servers:
        inferred["dev_server"] = servers
        origins["tester.dev_server"] = origin
    has_server = bool(explicit.get("dev_server") or servers)
    if not has_server and "base_url" not in explicit and pw.base_url:
        inferred["base_url"] = pw.base_url
        origins["tester.base_url"] = pw.file or ""
    frontend = has_server or "base_url" in explicit or "base_url" in inferred
    if pw.file and frontend and not _BROWSER_KEYS & set(explicit):
        browser = _inferred_browser(pw, notes)
        inferred.update(browser)
        origins.update({f"tester.{key}": pw.file for key in browser})
    return inferred, origins, notes


def command_defaults(root: Path, explicit: dict) -> DetectedCommands:
    """Supply only missing keys; explicit empty lists and tester opts win."""
    detected = detect_commands(root)
    defaults = {
        key: value
        for key, value in detected.defaults.items()
        if key not in explicit and key != "tester"
    }
    tester = explicit.get("tester", {})
    explicit_tester = tester if isinstance(tester, dict) else {}
    inferred, inferred_origins, notes = _tester_inference(Path(root), explicit_tester)
    merged = {
        **(
            detected.defaults.get("tester", {})
            if "tests" not in explicit_tester
            else {}
        ),
        **inferred,
    }
    if merged and (isinstance(tester, dict) or "tester" not in explicit):
        defaults["tester"] = merged
    origins = {
        key: value
        for key, value in detected.origins.items()
        if key != "tester.tests" or "tests" in merged
        if key.partition(".")[0] in defaults
    }
    origins.update(inferred_origins)
    return DetectedCommands(defaults, origins, (*detected.ambiguous, *notes))


def setup_defaults(root: Path, explicit: dict) -> DetectedCommands:
    """Include lazy MCP references; never copy registrations or credentials."""
    from meow.project.mcp_registry import discover_provider_references

    detected = command_defaults(root, explicit)
    defaults, origins = dict(detected.defaults), dict(detected.origins)
    registry = discover_provider_references(root)
    for provider, reference in registry["references"].items():
        if _provider_missing(explicit, provider):
            defaults[provider] = {"mcp": {"from": reference["from"]}}
            launcher = registry.get("launchers", {}).get(provider, "")
            origins[f"{provider}.mcp"] = (
                f"Claude MCP registration '{reference['from']}'"
                + (f", launches {launcher}" if launcher else "")
            )
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
