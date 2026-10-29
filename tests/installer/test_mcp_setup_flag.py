"""MCP setup is opt-in for the installer and onboarding, and reports honestly."""

import json
import subprocess

import pytest

from meow.cli.cli import cli_main
from meow.installer import _runtime
from meow.project.mcp_setup import SetupResult


def stub_setup(monkeypatch, module, status):
    calls = []

    def fake(providers, environ, **_kwargs):
        calls.append(list(providers))
        return [SetupResult(name, status, "detail") for name in providers]

    monkeypatch.setattr(module, "setup_providers", fake)
    return calls


@pytest.mark.parametrize(
    "outcome", [("registered", 0), ("already_registered", 0), ("no_launcher", 1)]
)
def test_installer_flag_registers_without_prompting(
    tmp_path, monkeypatch, capsys, outcome
):
    status, code = outcome
    monkeypatch.setattr(_runtime, "register_checkout_plugin", lambda *args: None)
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("prompted"))
    monkeypatch.setattr(
        _runtime, "_setup_projects", lambda: pytest.fail("project setup ran")
    )
    calls = stub_setup(monkeypatch, _runtime, status)
    assert (
        _runtime.main(["--repo-dir", str(tmp_path), "--mcp", "atlassian,gitlab"])
        == code
    )
    assert calls == [["jira", "gitlab"]]
    assert "Jira: detail" in capsys.readouterr().out


def test_interactive_offer_declined_registers_nothing(monkeypatch):
    monkeypatch.setattr(
        _runtime, "setup_provider", lambda *a, **k: pytest.fail("registered")
    )
    _runtime._offer_mcp(lambda _prompt: "n", print)


def test_interactive_offer_asks_per_provider(monkeypatch):
    answers = iter(["y", "y", "n", "n"])
    registered = []

    def fake(spec, environ, **_kwargs):
        registered.append(spec.provider)
        return SetupResult(spec.provider, "registered", "done")

    monkeypatch.setattr(_runtime, "setup_provider", fake)
    lines = []
    _runtime._offer_mcp(lambda _prompt: next(answers), lines.append)
    assert registered == ["jira"]
    assert lines == ["  Jira: done"]


@pytest.mark.parametrize("outcome", [("registered", 0), ("no_credentials", 1)])
def test_onboard_flag_reports_json_and_exit_status(
    tmp_path, monkeypatch, capsys, outcome
):
    status, code = outcome
    from meow.cli import onboard_cli

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    calls = stub_setup(monkeypatch, onboard_cli, status)
    with pytest.raises(SystemExit) as result:
        cli_main([
            "onboard",
            "--non-interactive",
            "--work-dir",
            str(tmp_path),
            "--mcp",
            "jira",
        ])
    assert result.value.code == code
    assert calls == [["jira"]]
    assert json.loads(capsys.readouterr().out)["mcp"][0]["status"] == status


def test_onboard_prefills_site_url_from_shared_config(tmp_path, monkeypatch):
    from meow.cli.onboard_cli import _setup_environment

    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow/config.toml").write_text(
        '[jira]\nurl = "https://acme.atlassian.net"\n'
    )
    monkeypatch.setenv("JIRA_URL", "https://mine.example")
    assert _setup_environment(tmp_path)["JIRA_URL"] == "https://mine.example"
    monkeypatch.delenv("JIRA_URL")
    assert _setup_environment(tmp_path)["JIRA_URL"] == "https://acme.atlassian.net"


def test_mcp_command_registers_without_project_onboarding(
    tmp_path, monkeypatch, capsys
):
    from meow.cli import mcp_cli

    calls = stub_setup(monkeypatch, mcp_cli, "registered")
    with pytest.raises(SystemExit) as result:
        cli_main(["mcp", "atlassian"])
    assert result.value.code == 0
    assert calls == [["jira"]]
    assert "Jira: detail" in capsys.readouterr().out


def test_prompts_show_examples_for_site_and_username(monkeypatch):
    from meow.project import mcp_setup

    asked = []
    monkeypatch.setattr("builtins.input", lambda text: asked.append(text) or "x")
    for name in ("JIRA_URL", "JIRA_USERNAME", "GITLAB_API_URL", "SOMETHING_ELSE"):
        mcp_setup.prompt_value(name, False)
    assert "e.g. https://yourcompany.atlassian.net" in asked[0]
    assert "e.g. you@yourcompany.com" in asked[1]
    assert "e.g. https://gitlab.com/api/v4" in asked[2]
    assert asked[3] == "SOMETHING_ELSE: "


def test_bun_flag_enables_store_and_migrates_without_asking(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        _runtime, "_enable_bun", lambda output: calls.append("on") or True
    )
    monkeypatch.setattr(
        _runtime, "_migrate_registered", lambda: calls.append("migrate")
    )
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("asked"))
    monkeypatch.setattr(_runtime, "register_checkout_plugin", lambda *a: None)
    assert _runtime.main(["--repo-dir", str(tmp_path), "--bun"]) == 0
    assert calls == ["on", "migrate"]


def test_mcp_offer_survives_a_closed_input():
    lines = []

    def closed(_prompt):
        raise EOFError

    _runtime._offer_mcp(closed, lines.append)
    assert any("skipped MCP setup" in line for line in lines)


def test_confluence_offer_prompts_for_its_variables_and_shares_the_server(monkeypatch):
    answers = iter(["y", "n", "y", "n"])  # offer, Jira no, Confluence yes, GitLab no
    seen = []
    monkeypatch.setattr(
        "builtins.input", lambda text: seen.append(text) or "https://x.example"
    )

    def fake(spec, environ, **_kwargs):
        seen.append(environ["CONFLUENCE_API_TOKEN"])
        return SetupResult(spec.provider, "registered", "done")

    monkeypatch.setattr(_runtime, "setup_provider", fake)
    for name in ("CONFLUENCE_URL", "CONFLUENCE_USERNAME", "CONFLUENCE_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    _runtime._offer_mcp(lambda _prompt: next(answers), lambda _line: None)
    assert any(text.startswith("CONFLUENCE_API_TOKEN") for text in seen)
    assert seen[-1] == "https://x.example"


def test_secret_prompts_are_echoed_and_gitlab_url_gets_api_root(monkeypatch):
    from meow.project import mcp_setup

    monkeypatch.setattr(
        "getpass.getpass", lambda *_: pytest.fail("secrets must be visible")
    )
    monkeypatch.setattr("builtins.input", lambda _text: "tok")
    assert mcp_setup.prompt_value("GITLAB_PERSONAL_ACCESS_TOKEN", True) == "tok"
    values = mcp_setup._collect(
        mcp_setup.SPECS["gitlab"],
        {"GITLAB_PERSONAL_ACCESS_TOKEN": "t", "GITLAB_API_URL": "https://gl.example/"},
        None,
    )
    assert values["GITLAB_API_URL"] == "https://gl.example/api/v4"


def test_windows_wraps_npx_in_cmd(monkeypatch):
    from meow.project import mcp_setup

    monkeypatch.setattr(mcp_setup.os, "name", "nt")
    argv = mcp_setup.build_command(mcp_setup.SPECS["gitlab"], {})
    assert argv[argv.index("--") + 1 : argv.index("--") + 4] == ["cmd", "/c", "npx"]
