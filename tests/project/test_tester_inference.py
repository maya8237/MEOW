import json
import subprocess
from unittest.mock import patch

import pytest

from meow.project.command_detection import command_defaults
from meow.project.config_schema import _normalize_tester_config

WEB_SERVER = """
export default {
  webServer: { command: 'npm run dev', url: 'http://localhost:3000', timeout: 90_000 },
};
"""


def infer(root, explicit=None):
    detected = command_defaults(root, explicit or {})
    return detected.defaults.get("tester", {}), detected


def playwright(root, text):
    (root / "playwright.config.ts").write_text(text, encoding="utf-8")


def package(root, scripts, deps=None, manager=None):
    body = {"scripts": scripts, "devDependencies": deps or {}}
    if manager:
        body["packageManager"] = manager
    (root / "package.json").write_text(json.dumps(body), encoding="utf-8")


def test_web_server_gives_dev_server_and_the_playwright_browser(tmp_path):
    playwright(tmp_path, WEB_SERVER)
    tester, detected = infer(tmp_path)
    assert tester["dev_server"] == [
        {
            "command": "npm run dev",
            "ready_url": "http://localhost:3000",
            "cwd": ".",
            "startup_timeout": 90.0,
        }
    ]
    assert tester["browser_playwright"] is True
    assert "playwright.config.ts" in detected.origins["tester.dev_server"]
    assert _normalize_tester_config({"tester": tester})["dev_server"]


def test_port_only_server_becomes_a_loopback_url(tmp_path):
    playwright(tmp_path, "export default { webServer: { command: 'x', port: 3000 } }")
    tester, _ = infer(tmp_path)
    assert tester["dev_server"][0]["ready_url"] == "http://127.0.0.1:3000/"


def test_literal_channel_and_executable_are_inferred(tmp_path):
    playwright(tmp_path, WEB_SERVER.replace("};", "use: { channel: 'chrome' } };"))
    tester, _ = infer(tmp_path)
    assert tester["browser_channel"] == "chrome"
    assert "browser_playwright" not in tester
    playwright(
        tmp_path,
        WEB_SERVER.replace(
            "};", "use: { launchOptions: { executablePath: '/usr/bin/chrome' } } };"
        ),
    )
    tester, _ = infer(tmp_path)
    assert tester["browser_executable"] == "/usr/bin/chrome"


def test_chromium_channel_means_the_playwright_browser(tmp_path):
    playwright(tmp_path, WEB_SERVER.replace("};", "use: { channel: 'chromium' } };"))
    tester, _ = infer(tmp_path)
    assert tester["browser_playwright"] is True
    assert "browser_channel" not in tester


def test_conflicting_browsers_infer_no_selection_and_say_why(tmp_path):
    playwright(
        tmp_path,
        WEB_SERVER.replace(
            "};",
            "projects: [{use:{channel:'chrome'}},{use:{channel:'msedge'}}] };",
        ),
    )
    tester, detected = infer(tmp_path)
    assert not {"browser_channel", "browser_playwright"} & set(tester)
    assert any("browser" in note for note in detected.ambiguous)


def test_explicit_values_win_per_key(tmp_path):
    playwright(tmp_path, WEB_SERVER)
    tester, _ = infer(tmp_path, {"tester": {"dev_server": [{"command": "mine"}]}})
    assert "dev_server" not in tester
    tester, _ = infer(tmp_path, {"tester": {"browser_channel": "msedge"}})
    assert "browser_playwright" not in tester
    assert "browser_channel" not in tester


@pytest.mark.parametrize(
    ("manager", "command"),
    [
        ("npm@10", "npm run dev -- --port {port} --host 127.0.0.1"),
        ("pnpm@9", "pnpm run dev --port {port} --host 127.0.0.1"),
        ("bun@1", "bun run dev --port {port} --host 127.0.0.1"),
    ],
)
def test_vite_script_gives_a_port_placeholder_entry(tmp_path, manager, command):
    package(tmp_path, {"dev": "vite"}, {"vite": "^5"}, manager)
    tester, _ = infer(tmp_path)
    server = tester["dev_server"][0]
    assert server["command"] == command
    assert server["ready_url"] == "http://127.0.0.1:{port}/"
    assert _normalize_tester_config({"tester": tester})["dev_server"]
    assert "browser_playwright" not in tester


def test_next_script_uses_its_own_port_flag(tmp_path):
    package(tmp_path, {"dev": "next dev"}, {"next": "14"}, "pnpm@9")
    tester, _ = infer(tmp_path)
    assert tester["dev_server"][0]["command"] == "pnpm run dev -p {port}"


def test_unconfirmed_framework_infers_nothing_and_says_so(tmp_path):
    package(tmp_path, {"dev": "astro dev"}, {"astro": "4"})
    tester, detected = infer(tmp_path)
    assert "dev_server" not in tester
    assert any("port flag" in note for note in detected.ambiguous)


def test_unusable_inferred_server_is_dropped_so_config_still_loads(tmp_path):
    playwright(
        tmp_path,
        "export default { webServer: { command: 'x', port: 1, cwd: '../outside' } }",
    )
    tester, detected = infer(tmp_path)
    assert "dev_server" not in tester
    assert any("webServer" in note for note in detected.ambiguous)
    _normalize_tester_config({"tester": tester})


def test_non_literal_values_are_reported_as_could_not_read(tmp_path):
    playwright(
        tmp_path,
        "export default { webServer: { command: process.env.CMD, port: 1 } }",
    )
    tester, detected = infer(tmp_path)
    assert "dev_server" not in tester
    assert any("could not read" in note for note in detected.ambiguous)


def test_inference_executes_nothing_and_writes_nothing(tmp_path):
    playwright(tmp_path, WEB_SERVER)
    before = sorted(path.name for path in tmp_path.iterdir())

    def boom(*args, **kwargs):
        raise AssertionError("inference must not execute anything")

    with (
        patch.object(subprocess, "run", boom),
        patch.object(subprocess, "Popen", boom),
    ):
        infer(tmp_path)
    assert sorted(path.name for path in tmp_path.iterdir()) == before


def test_base_url_only_when_there_is_no_server_url(tmp_path):
    playwright(tmp_path, "export default { use: { baseURL: 'http://localhost:5173' } }")
    tester, _ = infer(tmp_path)
    assert tester["base_url"] == "http://localhost:5173"
    assert tester["browser_playwright"] is True
    playwright(tmp_path, WEB_SERVER.replace("};", "use: { baseURL: 'http://x:1' } };"))
    tester, _ = infer(tmp_path)
    assert "base_url" not in tester


def test_no_playwright_config_and_no_script_infers_nothing(tmp_path):
    tester, detected = infer(tmp_path)
    assert tester == {}
    assert not any(key.startswith("tester.") for key in detected.origins)
