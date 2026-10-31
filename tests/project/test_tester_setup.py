import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from meow.cli import status_cli
from meow.execution import sprint_runner
from meow.project.config import load_config
from meow.project.tester_setup import browser_default_notice, inferred_tester_lines

NOTICE = (
    "[tester] no browser selected; using Google Chrome (browser_channel = "
    '"chrome"). Set browser_channel, browser_executable or '
    "browser_playwright = true to choose another."
)
WEB_SERVER = "export default { webServer: { command: 'npm run dev', port: 3000 } };"


def project(tmp_path, toml="", playwright=None):
    (tmp_path / ".meow").mkdir(exist_ok=True)
    (tmp_path / ".meow" / "config.toml").write_text(toml, encoding="utf-8")
    if playwright:
        (tmp_path / "playwright.config.ts").write_text(playwright, encoding="utf-8")
    return load_config(tmp_path)


def test_defaults_to_chrome_for_a_base_url_or_dev_server_without_a_browser(tmp_path):
    config = project(tmp_path, '[tester]\nbase_url = "http://127.0.0.1:3000"\n')
    assert config["tester"]["browser_channel"] == "chrome"
    assert browser_default_notice(config) == NOTICE


def test_no_warning_with_a_selection_or_without_a_frontend(tmp_path):
    chosen = project(
        tmp_path, '[tester]\nbase_url = "http://x:1"\nbrowser_channel = "chrome"\n'
    )
    assert browser_default_notice(chosen) is None
    assert browser_default_notice(project(tmp_path, "")) is None


def test_an_inferred_selection_counts_as_a_selection(tmp_path):
    config = project(tmp_path, "", playwright=WEB_SERVER)
    assert config["tester"]["browser_playwright"] is True
    assert browser_default_notice(config) is None


def test_inferred_lines_show_values_with_their_sources(tmp_path):
    config = project(tmp_path, "", playwright=WEB_SERVER)
    lines = inferred_tester_lines(config)
    assert (
        "Tester dev_server: npm run dev (inferred from playwright.config.ts)" in lines
    )
    assert (
        "Tester browser_playwright: True (inferred from playwright.config.ts)" in lines
    )


def test_explicit_values_are_not_listed_as_inferred(tmp_path):
    config = project(tmp_path, '[tester]\nbrowser_channel = "chrome"\n')
    assert inferred_tester_lines(config) == []


def run_start(tmp_path, capsys, **options):
    with patch.object(sprint_runner, "_drive_sprint", AsyncMock()):
        asyncio.run(
            sprint_runner.run_sprint(tmp_path, None, "x", use_worktree=False, **options)
        )
    return capsys.readouterr().out


def test_run_start_prints_the_warning_once_and_does_not_fail(tmp_path, capsys):
    project(tmp_path, '[tester]\nbase_url = "http://127.0.0.1:3000"\n')
    out = run_start(tmp_path, capsys)
    assert out.count(NOTICE) == 1


def test_run_start_is_quiet_when_configured_or_tests_are_disabled(tmp_path, capsys):
    project(tmp_path, '[tester]\nbase_url = "http://x:1"\nbrowser_playwright = true\n')
    assert NOTICE not in run_start(tmp_path, capsys)
    project(tmp_path, '[tester]\nbase_url = "http://127.0.0.1:3000"\n')
    assert NOTICE not in run_start(tmp_path, capsys, force_disable_tests=True)


def test_status_prints_the_warning_and_inferred_values(tmp_path, capsys):
    project(tmp_path, '[tester]\nbase_url = "http://127.0.0.1:3000"\n')
    record = SimpleNamespace(id="r1", repo=str(tmp_path), worktree=str(tmp_path))
    with (
        patch.object(status_cli, "render", return_value="RENDERED"),
        patch.object(status_cli, "concerns_for_run", return_value=[]),
    ):
        status_cli._print_run(record, False, [])
    out = capsys.readouterr().out
    assert "RENDERED" in out
    assert NOTICE in out
    project(tmp_path, "", playwright=WEB_SERVER)
    with (
        patch.object(status_cli, "render", return_value="RENDERED"),
        patch.object(status_cli, "concerns_for_run", return_value=[]),
    ):
        status_cli._print_run(record, False, [])
    assert "inferred from playwright.config.ts" in capsys.readouterr().out


def test_status_survives_an_invalid_config(tmp_path, capsys):
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow" / "config.toml").write_text("[tester]\nbogus = 1\n")
    record = SimpleNamespace(id="r1", repo=str(tmp_path), worktree=str(tmp_path))
    with (
        patch.object(status_cli, "render", return_value="RENDERED"),
        patch.object(status_cli, "concerns_for_run", return_value=[]),
    ):
        status_cli._print_run(record, False, [])
    assert "RENDERED" in capsys.readouterr().out
    assert Path(tmp_path).exists()


SKIP = "PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS"


def test_real_browsers_skip_playwrights_library_check_by_default():
    from meow.project.tester_setup import effective_browser_env

    assert effective_browser_env({"browser_channel": "chrome"}) == {SKIP: "true"}
    assert effective_browser_env({"browser_executable": "/x/chrome"}) == {SKIP: "true"}


def test_playwrights_own_browser_keeps_the_library_check():
    from meow.project.tester_setup import effective_browser_env

    assert effective_browser_env({"browser_playwright": True}) == {}
    assert effective_browser_env({}) == {}


def test_a_configured_value_overrides_the_default():
    from meow.project.tester_setup import effective_browser_env

    tester = {
        "browser_channel": "chrome",
        "browser_env": {SKIP: "false", "PLAYWRIGHT_A": "1"},
    }
    assert effective_browser_env(tester) == {SKIP: "false", "PLAYWRIGHT_A": "1"}
