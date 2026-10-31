import asyncio
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from meow.infrastructure import test_runner as runner
from meow.infrastructure import tester_preflight as preflight
from meow.project.config_models import DevServerCommand

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
    launched = fakes.get("launch")
    loaded = fakes.get("load", (200, 12))
    seen = fakes.setdefault("seen", {})

    def resolve(active):
        seen["playwright"] = active
        return version

    def launch(active, tester):
        seen["browser"] = active
        return launched

    with (
        patch.object(preflight, "shutil", SimpleNamespace(which=which.get)),
        patch.object(preflight, "_resolve_playwright", resolve),
        patch.object(preflight, "detect_browser", lambda: browser),
        patch.object(preflight, "_launch_browser", launch),
        patch.object(preflight, "_load_page", fakes.get("page") or (lambda *a: loaded)),
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
    missing = run(tmp_path, cfg)["browser_config"]
    assert missing.status == "fail"
    assert missing.snippet.startswith("[tester]")
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


def test_ports_check_passes_on_a_real_loopback_round_trip(tmp_path):
    cfg = config(base_url="http://x:1", browser_playwright=True)
    assert run(tmp_path, cfg)["ports"].status == "ok"


def test_ports_check_fails_cleanly_with_code_and_hint(tmp_path):
    cfg = config(base_url="http://x:1", browser_playwright=True)
    with (
        patch.object(runner, "_free_port", return_value=None),
        patch.object(
            runner, "_bindable", return_value="PermissionError [10013]: denied"
        ),
    ):
        check = run(tmp_path, cfg)["ports"]
    assert check.status == "fail"
    assert "10013" in check.detail
    assert "excludedportrange" in check.fix


def completed(code, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


def test_browser_launch_passes_options_and_env_and_reports_failure(tmp_path):
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(argv=argv, env=kwargs["env"])
        return completed(0, "ok 1.2")

    tester = {**BASE, "browser_channel": "chrome", "browser_env": {"PLAYWRIGHT_X": "1"}}
    node = SimpleNamespace(which=lambda name: "/usr/bin/node")
    with (
        patch.object(preflight, "shutil", node),
        patch.object(preflight, "_global_node_path", lambda: ""),
        patch.object(preflight.subprocess, "run", fake_run),
    ):
        assert preflight._launch_browser(tmp_path, tester) is None
    assert seen["env"]["MEOW_LAUNCH"] == '{"channel": "chrome"}'
    assert seen["env"]["PLAYWRIGHT_X"] == "1"
    message = "browserType.launch: Chromium distribution 'chrome' is not found at /x"
    failed = completed(1, "", message + "\nmore")
    with (
        patch.object(preflight, "shutil", node),
        patch.object(preflight, "_global_node_path", lambda: ""),
        patch.object(preflight.subprocess, "run", lambda *a, **k: failed),
    ):
        assert preflight._launch_browser(tmp_path, tester) == message


def test_browser_check_fails_with_the_launch_error_and_a_fix(tmp_path):
    cfg = config(base_url="http://x:1", browser_channel="chrome")
    checks = run(
        tmp_path, cfg, launch="Chromium distribution 'chrome' is not found at /x"
    )
    assert checks["browser"].status == "fail"
    assert "is not found" in checks["browser"].detail
    assert "browser_executable" in checks["browser"].fix
    assert checks["page"].status == "skipped"


def server_config(tmp_path):
    return DevServerCommand(
        Path("."),
        sys.executable,
        ("-m", "http.server", "{port}", "--bind", "127.0.0.1"),
        None,
        "http://127.0.0.1:{port}/",
        15,
    )


def port_closed(url):
    port = int(url.rsplit(":", 1)[1].strip("/"))
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


@pytest.mark.parametrize(
    ("loaded", "status"),
    [
        ((200, 12), "ok"),
        ((500, 12), "fail"),
        ((200, 0), "fail"),
        ("net::ERR_X", "fail"),
    ],
)
def test_page_check_uses_the_relocated_url_and_always_stops_the_server(
    tmp_path, loaded, status
):
    urls = []

    def page(active, tester, url):
        urls.append(url)
        return loaded

    cfg = config(dev_server=[server_config(tmp_path)], browser_playwright=True)
    check = run(tmp_path, cfg, page=page)["page"]
    assert check.status == status
    assert "{port}" not in urls[0]
    assert port_closed(urls[0])


def test_page_check_is_skipped_without_a_url(tmp_path):
    cfg = config(browser={"kind": "command"}, browser_playwright=True)
    assert run(tmp_path, cfg)["page"].status == "skipped"


def test_page_check_reports_a_server_that_never_starts(tmp_path):
    broken = DevServerCommand(
        Path("."),
        sys.executable,
        ("-c", "import time;time.sleep(30)"),
        None,
        "http://127.0.0.1:{port}/",
        1,
    )
    cfg = config(dev_server=[broken], browser_playwright=True)
    check = run(tmp_path, cfg)["page"]
    assert check.status == "fail"
    assert "port flag" in check.fix


CHROMIUM = Path("/opt/pw-browsers/chromium-1243/chrome-linux64/chrome")


@pytest.mark.skipif(
    not (shutil.which("node") and CHROMIUM.is_file()),
    reason="needs node, Playwright and a Chromium binary",
)
def test_full_preflight_with_a_real_browser_by_absolute_path(tmp_path):
    if preflight._resolve_playwright(tmp_path) is None:
        pytest.skip("playwright is not resolvable")
    (tmp_path / "index.html").write_text("<h1>hello</h1>")
    cfg = config(
        dev_server=[server_config(tmp_path)],
        browser_executable=str(CHROMIUM),
        browser_env={"PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS": "true"},
    )
    checks = asyncio.run(preflight.run_preflight(tmp_path, cfg))
    assert {c.name: c.status for c in checks} == {
        name: "ok"
        for name in (
            "target",
            "browser_config",
            "runtime",
            "playwright",
            "ports",
            "browser",
            "page",
        )
    }


def inner_server(directory="frontend"):
    return DevServerCommand(
        Path(directory),
        sys.executable,
        ("-m", "http.server", "{port}", "--bind", "127.0.0.1"),
        None,
        "http://127.0.0.1:{port}/",
        15,
    )


def test_node_checks_run_in_the_folder_of_the_dev_server(tmp_path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text("{}")
    cfg = config(dev_server=[inner_server()], browser_playwright=True)
    seen = {}
    run(tmp_path, cfg, seen=seen)
    assert seen["playwright"] == tmp_path / "frontend"
    assert seen["browser"] == tmp_path / "frontend"


def test_node_checks_use_the_repo_root_without_an_inner_project(tmp_path):
    cfg = config(base_url="http://x:1", browser_playwright=True)
    seen = {}
    run(tmp_path, cfg, seen=seen)
    assert seen["playwright"] == tmp_path


def test_a_dev_server_cwd_outside_the_repo_falls_back_to_the_root(tmp_path):
    cfg = config(dev_server=[inner_server("../elsewhere")], browser_playwright=True)
    seen = {}
    run(tmp_path, cfg, seen=seen)
    assert seen["playwright"] == tmp_path


def test_target_snippet_names_the_inner_folder(tmp_path):
    inner = tmp_path / "frontend"
    inner.mkdir()
    (inner / "package.json").write_text('{"scripts": {"dev": "vite"}}')
    (inner / "bun.lock").write_text("")
    snippet = run(tmp_path, config())["target"].snippet
    assert 'cwd = "frontend"' in snippet
    assert 'command = "bun run dev --port {port}"' in snippet
