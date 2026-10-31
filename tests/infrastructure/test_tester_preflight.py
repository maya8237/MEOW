import asyncio
import re
from unittest.mock import patch

import pytest

from meow.infrastructure import tester_preflight as preflight

BASE = {
    "tests": [],
    "dev_server": [],
    "mcp": [],
    "base_url": None,
    "browser": None,
    "browser_channel": None,
    "browser_executable": None,
    "browser_playwright": False,
    "browser_env": {},
}


def config(**tester):
    return {"tester": {**BASE, **tester}}


def run(root, cfg, **fakes):
    """Run the preflight with node, Playwright and the system browser faked."""
    which = {"node": fakes.get("node", "/usr/bin/node"), "bun": None}
    version, browser = fakes.get("pw", "1.63.0"), fakes.get("browser")

    with (
        patch.object(preflight.shutil, "which", lambda name: which.get(name)),
        patch.object(preflight, "_resolve_playwright", lambda active: version),
        patch.object(preflight, "detect_browser", lambda: browser),
    ):
        return {c.name: c for c in asyncio.run(preflight.run_preflight(root, cfg))}


def test_no_target_fails_with_a_port_placeholder_skeleton(tmp_path):
    checks = run(tmp_path, config())
    assert checks["target"].status == "fail"
    snippet = checks["target"].snippet
    assert "[[tester.dev_server]]" in snippet
    assert "{port}" in snippet
    assert checks["browser_config"].status == "skipped"


def test_dev_command_hint_comes_from_package_json(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"dev": "vite"}}')
    (tmp_path / "bun.lock").write_text("")
    checks = run(tmp_path, config())
    assert 'command = "bun run dev --port {port}"' in checks["target"].snippet


def test_no_selection_suggests_the_detected_browser(tmp_path):
    cfg = config(base_url="http://127.0.0.1:3000")
    checks = run(tmp_path, cfg, browser=("chrome", "/opt/google/chrome/chrome"))
    assert checks["browser_config"].status == "fail"
    assert checks["browser_config"].snippet == '[tester]\nbrowser_channel = "chrome"'


def test_no_selection_and_no_browser_lists_the_alternatives(tmp_path):
    checks = run(tmp_path, config(base_url="http://127.0.0.1:3000"))
    snippet = checks["browser_config"].snippet
    for key in ("browser_channel", "browser_executable", "browser_playwright"):
        assert f"# {key}" in snippet


@pytest.mark.parametrize(
    "selection",
    [
        {"browser_channel": "chrome"},
        {"browser_playwright": True},
    ],
)
def test_a_single_selection_passes(tmp_path, selection):
    cfg = config(base_url="http://127.0.0.1:3000", **selection)
    assert run(tmp_path, cfg)["browser_config"].status == "ok"


def test_executable_must_exist_and_be_a_file(tmp_path):
    cfg = config(base_url="http://x:1", browser_executable=str(tmp_path / "nope"))
    assert run(tmp_path, cfg)["browser_config"].status == "fail"
    cfg = config(base_url="http://x:1", browser_executable=str(tmp_path))
    assert run(tmp_path, cfg)["browser_config"].status == "fail"
    chrome = tmp_path / "chrome"
    chrome.write_text("")
    cfg = config(base_url="http://x:1", browser_executable=str(chrome))
    assert run(tmp_path, cfg)["browser_config"].status == "ok"


def test_runtime_needs_node_or_bun_and_playwright_follows(tmp_path):
    cfg = config(base_url="http://x:1", browser_playwright=True)
    checks = run(tmp_path, cfg, node=None)
    assert checks["runtime"].status == "fail"
    assert checks["playwright"].status == "skipped"
    checks = run(tmp_path, cfg, pw=None)
    assert checks["runtime"].status == "ok"
    assert checks["playwright"].status == "fail"
    assert "playwright" in checks["playwright"].fix


def test_force_disable_tests_skips_everything(tmp_path):
    cfg = config(base_url="http://x:1", browser_playwright=True)
    cfg["_force_disable_tests"] = True
    checks = run(tmp_path, cfg)
    assert {c.status for c in checks.values()} == {"skipped"}


def test_no_snippet_contains_a_guessed_port(tmp_path):
    for cfg in (config(), config(base_url="http://x:1")):
        for check in run(tmp_path, cfg).values():
            assert not re.search(r":\d{2,5}", check.snippet)
