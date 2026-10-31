"""Tester preflight: proves the setup works before any agent runs."""

import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from meow.project.command_detection import dev_command_hint

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


def _resolve_playwright(active_dir: Path) -> str | None:
    """Playwright's version when ``require('playwright')`` resolves, else None."""
    runtime = shutil.which("node") or shutil.which("bun")
    if runtime is None:
        return None
    script = "console.log(require('playwright/package.json').version)"
    environments = [os.environ.copy()]
    npm = shutil.which("npm")
    if npm:
        root = subprocess.run(
            [npm, "root", "-g"], capture_output=True, text=True, timeout=_PROBE_TIMEOUT
        ).stdout.strip()
        environments.append({**os.environ, "NODE_PATH": root})
    for env in environments:
        try:
            done = subprocess.run(
                [runtime, "-e", script],
                cwd=active_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if done.returncode == 0 and done.stdout.strip():
            return done.stdout.strip()
    return None


def _target(active_dir: Path, tester: dict) -> PreflightCheck:
    if tester.get("base_url") or tester.get("dev_server") or tester.get("browser"):
        return PreflightCheck("target", "ok", "a page to test is configured", "")
    hint = dev_command_hint(active_dir) or "<your dev command>"
    snippet = (
        "[[tester.dev_server]]\n"
        f'command = "{hint} --port ' + "{port}" + '"  # confirm your port flag\n'
        'ready_url = "http://127.0.0.1:' + "{port}" + '/"'
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
            "Point browser_executable at the browser binary itself.",
        )
    return PreflightCheck("browser_config", "ok", f"selected: {chosen[0]}", "")


def _runtime(active_dir: Path, tester: dict) -> PreflightCheck:
    found = shutil.which("node") or shutil.which("bun")
    if found:
        return PreflightCheck("runtime", "ok", found, "")
    return PreflightCheck(
        "runtime", "fail", "neither node nor bun is on PATH", "Install Node.js or Bun."
    )


def _playwright(active_dir: Path, tester: dict) -> PreflightCheck:
    version = _resolve_playwright(active_dir)
    if version:
        return PreflightCheck("playwright", "ok", f"playwright {version}", "")
    return PreflightCheck(
        "playwright",
        "fail",
        "playwright could not be resolved from the project or globally",
        "Run `bun add -d playwright` (or `npm i -D playwright`).",
    )


_CHECKS = {
    "target": _target,
    "browser_config": _browser_config,
    "runtime": _runtime,
    "playwright": _playwright,
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
            results[name] = await asyncio.to_thread(check, active_dir, tester)
    return list(results.values())
