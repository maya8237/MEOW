"""Tester preflight: proves the setup works before any agent runs."""

import asyncio
import json
import os
import shutil
import socket
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from http import HTTPStatus
from pathlib import Path

from meow.agents.tester import browser_launch_options
from meow.infrastructure import test_runner as runner
from meow.project.command_detection import dev_command_hint
from meow.project.tester_setup import effective_browser_env

_BROWSERS = (
    (
        "chrome",
        ("google-chrome", "google-chrome-stable", "chrome"),
        (
            "/opt/google/chrome/chrome",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        ),
    ),
    (
        "msedge",
        ("microsoft-edge", "msedge"),
        (
            "/opt/microsoft/msedge/msedge",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        ),
    ),
)
_SELECTION = ("browser_channel", "browser_executable", "browser_playwright")
_DEPENDS = {
    "browser_config": ("target",),
    "playwright": ("runtime",),
    "browser": ("browser_config", "runtime", "playwright"),
    "page": ("target", "ports", "browser"),
}
_PROBE_TIMEOUT = 30


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    status: str  # "ok" | "fail" | "skipped"
    detail: str
    fix: str
    snippet: str = ""


def detect_browser() -> tuple[str, str] | None:
    """(channel, path) of an installed Chrome or Edge. Executes nothing."""
    for channel, names, paths in _BROWSERS:
        found = next((shutil.which(name) for name in names if shutil.which(name)), None)
        found = found or next(
            (p for p in map(os.path.expandvars, paths) if Path(p).is_file()), None
        )
        if found:
            return channel, found
    return None


@lru_cache(maxsize=1)
def _global_node_path() -> str:
    npm = shutil.which("npm")
    if not npm:
        return ""
    try:
        done = subprocess.run(
            [npm, "root", "-g"], capture_output=True, text=True, timeout=_PROBE_TIMEOUT
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _node(
    active_dir: Path, script: str, env: dict
) -> subprocess.CompletedProcess | str:
    """Run a script with node (or bun) in the project; an error string on failure."""
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        return "neither node nor bun is on PATH"
    full = {**os.environ, **env}
    if _global_node_path():
        full["NODE_PATH"] = os.pathsep.join(
            filter(None, [full.get("NODE_PATH"), _global_node_path()])
        )
    try:
        return subprocess.run(
            [runtime, "-e", script],
            cwd=active_dir,
            env=full,
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT * 2,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"{type(exc).__name__}: {exc}"


def _first_line(done: subprocess.CompletedProcess) -> str:
    lines = (done.stderr or done.stdout or "").strip().splitlines()
    return lines[0] if lines else f"exit code {done.returncode}"


def _resolve_playwright(active_dir: Path) -> str | None:
    """Playwright's version when ``require('playwright')`` resolves, else None."""
    done = _node(
        active_dir, "console.log(require('playwright/package.json').version)", {}
    )
    if isinstance(done, str) or done.returncode != 0 or not done.stdout.strip():
        return None
    return done.stdout.strip()


_LAUNCH_JS = """
const { chromium } = require("playwright");
const launch = { headless: true, ...JSON.parse(process.env.MEOW_LAUNCH) };
const fail = (e) => {
  console.error(String(e.message).split("\\n")[0]);
  process.exit(1);
};
"""
_LAUNCH_ONLY_JS = (
    _LAUNCH_JS
    + """
(async () => {
  const browser = await chromium.launch(launch);
  console.log("ok " + browser.version());
  await browser.close();
})().catch(fail);
"""
)
_LOAD_JS = (
    _LAUNCH_JS
    + """
(async () => {
  const browser = await chromium.launch(launch);
  const page = await browser.newPage();
  const response = await page.goto(process.env.MEOW_URL, { timeout: 20000 });
  const text = await page.innerText("body");
  const status = response ? response.status() : 0;
  console.log(JSON.stringify({ status, length: text.trim().length }));
  await browser.close();
})().catch(fail);
"""
)


def _browser_env(tester: dict, **extra: str) -> dict:
    options = browser_launch_options(tester) or {}
    return {
        **effective_browser_env(tester),
        "MEOW_LAUNCH": json.dumps(options),
        **extra,
    }


def _launch_browser(active_dir: Path, tester: dict) -> str | None:
    """None when the configured browser launches headless, else the first error line."""
    done = _node(active_dir, _LAUNCH_ONLY_JS, _browser_env(tester))
    if isinstance(done, str):
        return done
    return None if done.returncode == 0 else _first_line(done)


def _load_page(active_dir: Path, tester: dict, url: str) -> tuple[int, int] | str:
    """(HTTP status, visible body length) of the page, or an error line."""
    done = _node(active_dir, _LOAD_JS, _browser_env(tester, MEOW_URL=url))
    if isinstance(done, str):
        return done
    if done.returncode != 0:
        return _first_line(done)
    try:
        data = json.loads(done.stdout.strip().splitlines()[-1])
        return int(data["status"]), int(data["length"])
    except (ValueError, KeyError, IndexError):
        return f"unreadable browser output: {done.stdout.strip()[:120]}"


def _target(active_dir: Path, tester: dict) -> PreflightCheck:
    if tester.get("base_url") or tester.get("dev_server") or tester.get("browser"):
        return PreflightCheck("target", "ok", "a page to test is configured", "")
    folder, hint = dev_command_hint(active_dir) or (".", "<your dev command>")
    cwd = "" if folder == "." else f'cwd = "{folder}"\n'
    snippet = (
        "[[tester.dev_server]]\n"
        + cwd
        + f'command = "{hint} --port '
        + "{port}"
        + '"  # confirm your port flag\n'
        + 'ready_url = "http://127.0.0.1:'
        + "{port}"
        + '/"'
    )
    return PreflightCheck(
        "target",
        "fail",
        "no base_url, [[tester.dev_server]] or [tester.browser] is configured",
        "Add a dev server (MEOW picks a free port for {port}) or set base_url.",
        snippet,
    )


def _selection_snippet() -> str:
    found = detect_browser()
    if found:
        return f'[tester]\nbrowser_channel = "{found[0]}"'
    return (
        "[tester]\n# Pick exactly one:\n"
        '# browser_channel = "chrome"\n'
        '# browser_executable = "/absolute/path/to/your/browser"\n'
        "# browser_playwright = true"
    )


def _browser_config(active_dir: Path, tester: dict) -> PreflightCheck:
    chosen = [key for key in _SELECTION if tester.get(key)]
    if not chosen:
        return PreflightCheck(
            "browser_config",
            "fail",
            "no browser is selected",
            "Set exactly one of browser_channel, browser_executable or "
            "browser_playwright = true under [tester].",
            _selection_snippet(),
        )
    executable = tester.get("browser_executable")
    if executable and not Path(executable).is_file():
        return PreflightCheck(
            "browser_config",
            "fail",
            f"browser_executable {executable} does not exist or is not a file",
            "Point browser_executable at the browser binary itself, or select "
            "an installed browser by channel.",
            _selection_snippet(),
        )
    return PreflightCheck("browser_config", "ok", f"selected: {chosen[0]}", "")


def _runtime(active_dir: Path, tester: dict) -> PreflightCheck:
    found = shutil.which("node") or shutil.which("bun")
    if found:
        return PreflightCheck("runtime", "ok", found, "")
    return PreflightCheck(
        "runtime", "fail", "neither node nor bun is on PATH", "Install Node.js or Bun."
    )


def _web_dir(active_dir: Path, tester: dict) -> Path:
    """The folder node should run in: the dev server's project, else the repo root."""
    for server in tester.get("dev_server", []):
        try:
            candidate = runner.resolve_command_cwd(active_dir, server.cwd)
        except (OSError, ValueError):
            continue
        if (candidate / "package.json").is_file():
            return candidate
    return active_dir


def _playwright(active_dir: Path, tester: dict) -> PreflightCheck:
    version = _resolve_playwright(_web_dir(active_dir, tester))
    if version:
        return PreflightCheck("playwright", "ok", f"playwright {version}", "")
    return PreflightCheck(
        "playwright",
        "fail",
        "playwright could not be resolved from the project or globally",
        "Run `bun add -d playwright` (or `npm i -D playwright`).",
    )


def _ports(active_dir: Path, tester: dict) -> PreflightCheck:
    port = runner._free_port("127.0.0.1")
    if port is None:
        reason = runner._bindable("127.0.0.1", 0) or "no candidate port could be bound"
        return PreflightCheck(
            "ports",
            "fail",
            f"cannot allocate a loopback port: {reason}",
            runner.PORT_HINT,
        )
    try:
        with socket.socket() as server, socket.socket() as client:
            server.bind(("127.0.0.1", port))
            server.listen(1)
            client.settimeout(2)
            client.connect(("127.0.0.1", port))
            server.accept()[0].close()
    except OSError as exc:
        return PreflightCheck(
            "ports",
            "fail",
            f"loopback round trip failed: {runner._describe(exc)}",
            runner.PORT_HINT,
        )
    return PreflightCheck("ports", "ok", f"loopback port {port} works", "")


def _browser(active_dir: Path, tester: dict) -> PreflightCheck:
    error = _launch_browser(_web_dir(active_dir, tester), tester)
    if error is None:
        return PreflightCheck("browser", "ok", "the configured browser launches", "")
    return PreflightCheck(
        "browser",
        "fail",
        error,
        "Install the selected browser, set browser_executable to its absolute path, "
        "or use browser_playwright = true after `bunx playwright install chromium`.",
    )


_PAGE_FIX = (
    "Check the dev server command and ready_url; if the server ignores "
    "{port}/PORT, pass the port with its port flag."
)


def _judge_page(url: str, outcome: tuple[int, int] | str) -> PreflightCheck:
    if isinstance(outcome, str):
        return PreflightCheck("page", "fail", outcome, "Check that the page loads.")
    status, length = outcome
    if not HTTPStatus.OK <= status < HTTPStatus.MULTIPLE_CHOICES:
        detail = f"{url} responded {status}"
    elif length == 0:
        detail = f"{url} responded {status} with an empty body"
    else:
        return PreflightCheck(
            "page", "ok", f"{url} responded {status} with content", ""
        )
    return PreflightCheck("page", "fail", detail, "Fix the page so it renders content.")


async def _page(active_dir: Path, tester: dict) -> PreflightCheck:
    owned, url = [], None
    try:
        for server in tester.get("dev_server", []):
            process, served = await runner._start_server_with_fallback(
                active_dir, server
            )
            owned.append(process)
            url = url or served
        url = url or tester.get("base_url")
        if not url:
            return PreflightCheck(
                "page", "skipped", "no URL to load; only a browser provider is set", ""
            )
        web = _web_dir(active_dir, tester)
        outcome = await asyncio.to_thread(_load_page, web, tester, url)
    except runner.VerificationSetupError as exc:
        return PreflightCheck("page", "fail", str(exc), _PAGE_FIX)
    finally:
        for process in reversed(owned):
            await runner._terminate(process)
    return _judge_page(url, outcome)


_CHECKS = {
    "target": _target,
    "browser_config": _browser_config,
    "runtime": _runtime,
    "playwright": _playwright,
    "ports": _ports,
    "browser": _browser,
    "page": _page,
}


async def run_preflight(active_dir: Path, config: dict) -> list[PreflightCheck]:
    """Run the checks in order; a check whose prerequisite failed is skipped."""
    tester = config.get("tester", {})
    if config.get("_force_disable_tests"):
        return [
            PreflightCheck(name, "skipped", "tests are disabled", "")
            for name in _CHECKS
        ]
    results: dict[str, PreflightCheck] = {}
    for name, check in _CHECKS.items():
        blocked = next(
            (d for d in _DEPENDS.get(name, ()) if results[d].status != "ok"), None
        )
        if blocked:
            results[name] = PreflightCheck(
                name, "skipped", f"skipped because {blocked} did not pass", ""
            )
        else:
            results[name] = (
                await check(active_dir, tester)
                if asyncio.iscoroutinefunction(check)
                else await asyncio.to_thread(check, active_dir, tester)
            )
    return list(results.values())
