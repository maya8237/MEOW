"""Conservative, read-only frontend and browser capability discovery."""

import json
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class FrontendEvidence:
    manifests: tuple[str, ...] = ()
    candidate_start_commands: tuple[str, ...] = ()
    readiness_urls: tuple[str, ...] = ()
    browser_tools: tuple[str, ...] = ()
    browser_test_dirs: tuple[str, ...] = ()
    frontend_detected: bool = False
    browser_testable: bool = False
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BrowserCapability:
    configured: bool
    start_command: str = ""
    readiness_url: str = ""
    provider: str = ""
    required: bool = False
    reason: str = ""


def browser_capability(config: dict) -> BrowserCapability:
    """Summarize a configured browser stage before executing project commands."""
    tester = config.get("tester", {})
    browser = tester.get("browser")
    if not isinstance(browser, dict):
        return BrowserCapability(False, reason="browser provider is not configured")
    servers = tester.get("dev_server", [])
    if not servers:
        return BrowserCapability(
            False,
            provider=str(browser.get("entrypoint", "")),
            required=bool(browser.get("required", False)),
            reason="start command and readiness URL are not configured",
        )
    server = servers[0]
    return BrowserCapability(
        True,
        server.command,
        server.ready_url,
        str(browser.get("entrypoint", "")),
        bool(browser.get("required", False)),
    )


def discover_frontend(project_dir: Path) -> FrontendEvidence:
    """Inspect common manifests without executing project commands.

    This deliberately reports candidates only.  A project is browser testable
    after the configured server and provider pass runtime preflight.
    """
    root = Path(project_dir).resolve()
    manifests: list[str] = []
    starts: list[str] = []
    urls: list[str] = []
    tools: list[str] = []
    dirs: list[str] = []
    reasons: list[str] = []
    package = root / "package.json"
    if package.is_file():
        manifests.append("package.json")
        try:
            data = json.loads(package.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            reasons.append("package_manifest_unreadable")
            data = {}
        scripts = data.get("scripts", {}) if isinstance(data, dict) else {}
        if isinstance(scripts, dict):
            for name in ("dev", "start", "serve", "preview"):
                if isinstance(scripts.get(name), str):
                    starts.append(f"npm run {name}")
            all_text = " ".join(str(v) for v in scripts.values())
        else:
            all_text = ""
        deps = {}
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            if isinstance(data, dict) and isinstance(data.get(key), dict):
                deps.update(data[key])
        for name in deps:
            low = name.lower()
            if any(
                token in low
                for token in ("playwright", "cypress", "webdriver", "puppeteer")
            ):
                tools.append(name)
        if any(
            token in all_text.lower()
            for token in ("playwright", "cypress", "webdriver", "puppeteer")
        ):
            tools.append("browser-script")
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        manifests.append("pyproject.toml")
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            reasons.append("pyproject_unreadable")
            data = {}
        project = data.get("project", {}) if isinstance(data, dict) else {}
        deps = project.get("dependencies", []) if isinstance(project, dict) else []
        text = " ".join(str(item) for item in deps) if isinstance(deps, list) else ""
        if any(
            token in text.lower() for token in ("selenium", "playwright", "cypress")
        ):
            tools.append("python-browser-dependency")
    for candidate in ("tests", "test", "e2e", "tests/e2e", "playwright", "cypress"):
        path = root / candidate
        if path.is_dir():
            dirs.append(candidate)
    frontend = bool(manifests and (starts or tools or dirs))
    if frontend and not starts:
        reasons.append("missing_start_command")
    if not urls:
        reasons.append("missing_readiness_url")
    if not tools and not dirs:
        reasons.append("missing_browser_provider")
    return FrontendEvidence(
        tuple(dict.fromkeys(manifests)),
        tuple(dict.fromkeys(starts)),
        tuple(urls),
        tuple(dict.fromkeys(tools)),
        tuple(dict.fromkeys(dirs)),
        frontend,
        False,
        tuple(dict.fromkeys(reasons)),
    )
