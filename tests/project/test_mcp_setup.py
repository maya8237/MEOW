"""User-scope MCP setup never installs launchers or exposes credentials."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from meow.project.mcp_registry import provider_mcp_config
from meow.project.mcp_setup import (
    SPECS,
    build_command,
    parse_providers,
    setup_provider,
    setup_providers,
)

SECRET = "s3cret-token"
JIRA_ENV = {
    "JIRA_URL": "https://acme.atlassian.net",
    "JIRA_USERNAME": "me@example.com",
    "JIRA_API_TOKEN": SECRET,
}


class Recorder:
    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, argv, **_kwargs):
        self.calls.append(argv)
        return SimpleNamespace(returncode=self.returncode)


def found(*names):
    return lambda name: f"/bin/{name}" if name in names else None


def write_user_servers(home, servers):
    (home / ".claude.json").write_text(json.dumps({"mcpServers": servers}))


def test_jira_uses_mcp_atlassian_and_its_own_variable_names(tmp_path):
    run = Recorder()
    result = setup_provider(
        SPECS["jira"], JIRA_ENV, home=tmp_path, run=run, which=found("claude", "uvx")
    )
    assert result.status == "registered"
    add, warm = run.calls
    assert add[:6] == ["claude", "mcp", "add", "--scope", "user", "atlassian"]
    assert add[-3:] == ["--", "uvx", "mcp-atlassian"]
    assert f"JIRA_API_TOKEN={SECRET}" in add
    assert warm == ["uvx", "mcp-atlassian", "--help"]
    assert SECRET not in json.dumps(result.to_dict())


def test_server_datacenter_token_is_an_alternative(tmp_path):
    environ = {"JIRA_URL": "https://jira.example.com", "JIRA_PERSONAL_TOKEN": SECRET}
    run = Recorder()
    result = setup_provider(
        SPECS["jira"], environ, home=tmp_path, run=run, which=found("claude", "uvx")
    )
    assert result.status == "registered"
    assert "JIRA_USERNAME" not in " ".join(run.calls[0])


def test_github_passes_variables_through_docker_without_values():
    argv = build_command(
        SPECS["github"],
        {"GITHUB_PERSONAL_ACCESS_TOKEN": SECRET, "GITHUB_HOST": "https://gh.example"},
    )
    launch = argv[argv.index("--") + 1 :]
    assert launch == [
        "docker",
        "run",
        "-i",
        "--rm",
        "-e",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
        "-e",
        "GITHUB_HOST",
        "ghcr.io/github/github-mcp-server",
    ]
    assert SECRET not in launch


def test_existing_user_scope_server_is_left_alone(tmp_path):
    write_user_servers(
        tmp_path, {"my-jira": {"command": "uvx", "args": ["mcp-atlassian"]}}
    )
    run = Recorder()
    result = setup_provider(
        SPECS["jira"], JIRA_ENV, home=tmp_path, run=run, which=found("claude", "uvx")
    )
    assert result.status == "already_registered"
    assert run.calls == []


@pytest.mark.parametrize(
    "available,status",
    [((), "no_claude"), (("claude",), "no_launcher")],
)
def test_missing_tools_are_reported_and_never_installed(tmp_path, available, status):
    run = Recorder()
    result = setup_provider(
        SPECS["jira"], JIRA_ENV, home=tmp_path, run=run, which=found(*available)
    )
    assert result.status == status
    assert result.failed
    assert run.calls == []


def test_missing_credentials_name_variables_not_values(tmp_path):
    run = Recorder()
    result = setup_provider(
        SPECS["jira"],
        {"JIRA_URL": "https://acme.atlassian.net"},
        home=tmp_path,
        run=run,
        which=found("claude", "uvx"),
    )
    assert result.status == "no_credentials"
    assert "JIRA_API_TOKEN" in result.detail
    assert run.calls == []


def test_prompt_supplies_missing_values_and_hides_secrets(tmp_path):
    asked = []

    def prompt(label, secret):
        asked.append((label, secret))
        return {"JIRA_URL": "https://acme.atlassian.net", "JIRA_USERNAME": "me"}.get(
            label, SECRET
        )

    run = Recorder()
    result = setup_provider(
        SPECS["jira"],
        {},
        prompt=prompt,
        home=tmp_path,
        run=run,
        which=found("claude", "uvx"),
    )
    assert result.status == "registered"
    assert ("JIRA_API_TOKEN", True) in asked
    assert ("JIRA_USERNAME", False) in asked


def test_failed_add_withholds_command_output(tmp_path):
    result = setup_provider(
        SPECS["jira"],
        JIRA_ENV,
        home=tmp_path,
        run=Recorder(returncode=2),
        which=found("claude", "uvx"),
    )
    assert result.status == "failed"
    assert SECRET not in result.detail


def test_add_that_cannot_run_is_a_failed_result(tmp_path):
    def broken(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("claude", 60)

    result = setup_provider(
        SPECS["jira"], JIRA_ENV, home=tmp_path, run=broken, which=found("claude", "uvx")
    )
    assert result.status == "failed"


def test_unsupported_provider_is_reported(tmp_path):
    [result] = setup_providers(["gitlab"], {}, home=tmp_path, which=found())
    assert result.status == "unsupported"
    assert "jira" in result.detail


def test_parse_providers():
    assert parse_providers("all") == ("jira", "github")
    assert parse_providers(" Jira, github,jira ") == ("jira", "github")


def test_runtime_uses_the_single_user_scope_server(tmp_path, monkeypatch):
    write_user_servers(
        tmp_path,
        {"atlassian": {"command": "uvx", "args": ["mcp-atlassian"], "env": {"A": "b"}}},
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    assert provider_mcp_config({}, "jira") == {
        "command": "uvx",
        "args": ["mcp-atlassian"],
        "env": {"A": "b"},
    }


def test_runtime_refuses_to_guess_between_user_scope_servers(tmp_path, monkeypatch):
    write_user_servers(
        tmp_path, {"jira-a": {"command": "x"}, "atlassian-b": {"command": "y"}}
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(ValueError, match="Several user-scope"):
        provider_mcp_config({}, "jira")


def test_runtime_fallback_ignores_project_mcp_json(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"atlassian": {"command": "evil"}}})
    )
    config = {"_project_root": str(tmp_path)}
    with pytest.raises(ValueError, match="meow onboard --mcp jira"):
        provider_mcp_config(config, "jira")
