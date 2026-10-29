"""User-scope MCP setup never installs launchers or exposes credentials."""

import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from meow.project.mcp_registry import provider_mcp_config
from meow.project.mcp_setup import (
    SPECS,
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
        SPECS["jira"],
        JIRA_ENV,
        home=tmp_path,
        run=run,
        which=found("claude", "mcp-atlassian"),
    )
    assert result.status == "registered"
    [add] = run.calls
    assert add[:6] == ["claude", "mcp", "add", "--scope", "user", "atlassian"]
    assert add[-2:] == ["--", "mcp-atlassian"]
    assert f"JIRA_API_TOKEN={SECRET}" in add
    assert SECRET not in json.dumps(result.to_dict())


def test_server_datacenter_token_is_an_alternative(tmp_path):
    environ = {"JIRA_URL": "https://jira.example.com", "JIRA_PERSONAL_TOKEN": SECRET}
    run = Recorder()
    result = setup_provider(
        SPECS["jira"],
        environ,
        home=tmp_path,
        run=run,
        which=found("claude", "mcp-atlassian"),
    )
    assert result.status == "registered"
    assert "JIRA_USERNAME" not in " ".join(run.calls[0])


def test_confluence_variables_are_forwarded_only_when_set(tmp_path):
    confluence = {
        "CONFLUENCE_URL": "https://acme.atlassian.net/wiki",
        "CONFLUENCE_USERNAME": "me@example.com",
        "CONFLUENCE_API_TOKEN": SECRET,
    }
    with_run, without_run = Recorder(), Recorder()
    which = found("claude", "mcp-atlassian")
    setup_provider(
        SPECS["jira"],
        {**JIRA_ENV, **confluence},
        home=tmp_path,
        run=with_run,
        which=which,
    )
    setup_provider(SPECS["jira"], JIRA_ENV, home=tmp_path, run=without_run, which=which)
    for name, value in confluence.items():
        assert f"{name}={value}" in with_run.calls[0]
    assert "CONFLUENCE" not in " ".join(without_run.calls[0])


def test_atlassian_ssl_verify_variables_are_forwarded_only_when_set(tmp_path):
    environ = {
        **JIRA_ENV,
        "JIRA_SSL_VERIFY": "false",
        "CONFLUENCE_SSL_VERIFY": "true",
    }
    with_run, without_run = Recorder(), Recorder()
    which = found("claude", "mcp-atlassian")
    setup_provider(SPECS["jira"], environ, home=tmp_path, run=with_run, which=which)
    setup_provider(SPECS["jira"], JIRA_ENV, home=tmp_path, run=without_run, which=which)
    assert "JIRA_SSL_VERIFY=false" in with_run.calls[0]
    assert "CONFLUENCE_SSL_VERIFY=true" in with_run.calls[0]
    assert "SSL_VERIFY" not in " ".join(without_run.calls[0])


def test_confluence_alone_is_enough(tmp_path):
    environ = {
        "CONFLUENCE_URL": "https://acme.atlassian.net/wiki",
        "CONFLUENCE_PERSONAL_TOKEN": SECRET,
    }
    run = Recorder()
    result = setup_provider(
        SPECS["jira"],
        environ,
        home=tmp_path,
        run=run,
        which=found("claude", "mcp-atlassian"),
    )
    assert result.status == "registered"
    assert "JIRA_URL" not in " ".join(run.calls[0])


def test_github_has_no_setup_yet(tmp_path):
    [result] = setup_providers(["github"], {}, home=tmp_path, which=found("claude"))
    assert result.status == "unsupported"
    assert result.failed


def test_existing_user_scope_server_is_left_alone(tmp_path):
    write_user_servers(
        tmp_path, {"my-jira": {"command": "uvx", "args": ["mcp-atlassian"]}}
    )
    run = Recorder()
    result = setup_provider(
        SPECS["jira"],
        JIRA_ENV,
        home=tmp_path,
        run=run,
        which=found("claude", "mcp-atlassian"),
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
        which=found("claude", "mcp-atlassian"),
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
        which=found("claude", "mcp-atlassian"),
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
        which=found("claude", "mcp-atlassian"),
    )
    assert result.status == "failed"
    assert SECRET not in result.detail


def test_add_that_cannot_run_is_a_failed_result(tmp_path):
    def broken(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("claude", 60)

    result = setup_provider(
        SPECS["jira"],
        JIRA_ENV,
        home=tmp_path,
        run=broken,
        which=found("claude", "mcp-atlassian"),
    )
    assert result.status == "failed"


def test_unsupported_provider_is_reported(tmp_path):
    [result] = setup_providers(["bitbucket"], {}, home=tmp_path, which=found())
    assert result.status == "unsupported"
    assert "jira" in result.detail


def test_parse_providers():
    assert parse_providers("all") == ("jira", "gitlab")
    assert parse_providers(" Jira, gitlab,jira ") == ("jira", "gitlab")
    assert parse_providers("atlassian,gitlab") == ("jira", "gitlab")


def test_runtime_uses_the_single_user_scope_server(tmp_path, monkeypatch):
    write_user_servers(
        tmp_path,
        {"atlassian": {"command": "uvx", "args": ["mcp-atlassian"], "env": {"A": "b"}}},
    )
    for name in ("HOME", "USERPROFILE"):  # Path.home() on Windows
        monkeypatch.setenv(name, str(tmp_path))
    assert provider_mcp_config({}, "jira") == {
        "command": "uvx",
        "args": ["mcp-atlassian"],
        "env": {"A": "b"},
    }


def test_runtime_refuses_to_guess_between_user_scope_servers(tmp_path, monkeypatch):
    write_user_servers(
        tmp_path, {"jira-a": {"command": "x"}, "atlassian-b": {"command": "y"}}
    )
    for name in ("HOME", "USERPROFILE"):  # Path.home() on Windows
        monkeypatch.setenv(name, str(tmp_path))
    with pytest.raises(ValueError, match="Several user-scope"):
        provider_mcp_config({}, "jira")


def test_runtime_fallback_ignores_project_mcp_json(tmp_path, monkeypatch):
    for name in ("HOME", "USERPROFILE"):  # Path.home() on Windows
        monkeypatch.setenv(name, str(tmp_path / "home"))
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"atlassian": {"command": "evil"}}})
    )
    config = {"_project_root": str(tmp_path)}
    with pytest.raises(ValueError, match="meow onboard --mcp atlassian"):
        provider_mcp_config(config, "jira")


def test_gitlab_uses_zereight_package_and_its_own_variable_names(tmp_path):
    environ = {
        "GITLAB_PERSONAL_ACCESS_TOKEN": SECRET,
        "GITLAB_API_URL": "https://gitlab.example.com/api/v4",
    }
    run = Recorder()
    result = setup_provider(
        SPECS["gitlab"], environ, home=tmp_path, run=run, which=found("claude", "npx")
    )
    assert result.status == "registered"
    add = run.calls[0]
    assert add[-4:] == ["--", "npx", "-y", "@zereight/mcp-gitlab"]
    assert f"GITLAB_PERSONAL_ACCESS_TOKEN={SECRET}" in add
    assert "GITLAB_API_URL=https://gitlab.example.com/api/v4" in add
    assert SECRET not in json.dumps(result.to_dict())


def test_gitlab_without_npx_is_reported_not_installed(tmp_path):
    result = setup_provider(
        SPECS["gitlab"], {}, home=tmp_path, run=Recorder(), which=found("claude")
    )
    assert result.status == "no_launcher"


def test_gitlab_site_url_prefills_the_api_root(tmp_path, monkeypatch):
    from meow.cli.onboard_cli import _setup_environment

    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow/config.toml").write_text(
        '[gitlab]\nurl = "https://gitlab.example.com/"\n'
    )
    monkeypatch.delenv("GITLAB_API_URL", raising=False)
    environ = _setup_environment(tmp_path)
    assert environ["GITLAB_API_URL"] == "https://gitlab.example.com/api/v4"


def test_runtime_environment_comes_from_the_user_scope_server(tmp_path, monkeypatch):
    from meow.project.mcp_registry import provider_environment

    write_user_servers(
        tmp_path,
        {"gitlab": {"command": "npx", "env": {"GITLAB_PERSONAL_ACCESS_TOKEN": "t"}}},
    )
    for name in ("HOME", "USERPROFILE"):  # Path.home() on Windows
        monkeypatch.setenv(name, str(tmp_path))
    config = {"_project_root": str(tmp_path)}
    assert provider_environment(config, "gitlab") == {
        "GITLAB_PERSONAL_ACCESS_TOKEN": "t"
    }
    assert provider_environment(config, "gitlab", resolve_reference=False) == {}
    assert provider_environment(config, "jira") == {}


def test_pipeline_accepts_the_servers_own_names_and_the_project_url(
    tmp_path, monkeypatch
):
    from meow.integrations.pipeline_gitlab import connect_gitlab

    write_user_servers(
        tmp_path,
        {
            "gitlab": {
                "command": "npx",
                "env": {
                    "GITLAB_PERSONAL_ACCESS_TOKEN": "tok",
                    "GITLAB_API_URL": "https://other.example.com/api/v4",
                },
            }
        },
    )
    for name in ("HOME", "USERPROFILE"):  # Path.home() on Windows
        monkeypatch.setenv(name, str(tmp_path))
    config = {"gitlab": {"url": "https://gitlab.example.com"}}
    client = connect_gitlab(("gitlab.example.com", "group/repo"), config, {})
    assert client.token == "tok"
    assert client.api == "https://gitlab.example.com/api/v4"


def test_project_url_identifies_a_self_hosted_gitlab_origin():
    from meow.execution.orchestration.pull_request import detect_provider

    config = {"gitlab": {"url": "https://code.example.com"}}
    assert detect_provider("git@code.example.com:group/repo.git", config) == "gitlab"
    assert detect_provider("git@code.example.com:group/repo.git", {}) is None


def test_native_verify_sees_a_user_scope_server(tmp_path, monkeypatch):
    from meow.native import native

    write_user_servers(
        tmp_path, {"atlassian": {"command": "uvx", "args": ["mcp-atlassian"]}}
    )
    for name in ("HOME", "USERPROFILE"):  # Path.home() on Windows
        monkeypatch.setenv(name, str(tmp_path))
    result = native._verify_jira({"_project_root": str(tmp_path)})
    assert result["configured"] is True
    assert "uvx" not in json.dumps(result)
    assert native._verify_github({})["configured"] is False


def test_site_mismatch_is_logged_without_failing(tmp_path, monkeypatch):
    from meow.project import mcp_registry

    write_user_servers(
        tmp_path,
        {"atlassian": {"command": "uvx", "env": {"JIRA_URL": "https://old.example"}}},
    )
    for name in ("HOME", "USERPROFILE"):  # Path.home() on Windows
        monkeypatch.setenv(name, str(tmp_path))
    warnings = []
    monkeypatch.setattr(
        mcp_registry.logger, "warning", lambda event, **fields: warnings.append(fields)
    )
    config = {"jira": {"url": "https://acme.atlassian.net"}}
    assert provider_mcp_config(config, "jira")["command"] == "uvx"
    assert warnings == [
        {
            "provider": "jira",
            "project_host": "acme.atlassian.net",
            "server_host": "old.example",
        }
    ]


def test_gap_report_marks_a_user_scope_server_detected(tmp_path, monkeypatch):
    from meow.project.onboarding import feature_gaps

    write_user_servers(
        tmp_path, {"atlassian": {"command": "uvx", "args": ["mcp-atlassian"]}}
    )
    for name in ("HOME", "USERPROFILE"):  # Path.home() on Windows
        monkeypatch.setenv(name, str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    gaps = feature_gaps(repo)
    assert gaps["jira"]["detected"] is True
    assert gaps["github"]["detected"] is False


class PipRecorder(Recorder):
    """Pretends pip creates the server's launcher in MEOW's tools environment."""

    def __init__(self, home, returncode=0):
        super().__init__(returncode)
        self.launcher = home / ".meow" / "tools" / "bin" / "mcp-atlassian"

    def __call__(self, argv, **kwargs):
        if "pip" in argv and not self.returncode:
            self.launcher.parent.mkdir(parents=True, exist_ok=True)
            self.launcher.write_text("")
        return super().__call__(argv, **kwargs)


@pytest.mark.skipif(os.name == "nt", reason="POSIX tools layout")
def test_missing_jira_server_is_pip_installed_when_allowed(tmp_path):
    run = PipRecorder(tmp_path)
    asked = []
    result = setup_provider(
        SPECS["jira"],
        JIRA_ENV,
        home=tmp_path,
        run=run,
        which=found("claude"),
        allow_install=lambda package: asked.append(package) or True,
    )
    assert result.status == "registered"
    assert asked == ["mcp-atlassian"]
    venv, pip, add = run.calls
    assert venv[1:3] == ["-m", "venv"]
    assert pip[-2:] == ["install", "mcp-atlassian"]
    assert add[-2:] == ["--", str(run.launcher)]
    assert SECRET not in json.dumps(result.to_dict())


def test_missing_jira_server_not_installed_unless_allowed(tmp_path):
    run = PipRecorder(tmp_path)
    for allow in (None, lambda _package: False):
        result = setup_provider(
            SPECS["jira"],
            JIRA_ENV,
            home=tmp_path,
            run=run,
            which=found("claude"),
            allow_install=allow,
        )
        assert result.status == "no_launcher"
        assert "--install-missing" in result.detail
    assert run.calls == []


def test_failed_pip_install_is_reported_without_registering(tmp_path):
    run = PipRecorder(tmp_path, returncode=1)
    result = setup_provider(
        SPECS["jira"],
        JIRA_ENV,
        home=tmp_path,
        run=run,
        which=found("claude"),
        allow_install=lambda _package: True,
    )
    assert result.status == "no_launcher"
    assert "pip install mcp-atlassian" in result.detail
    assert all("mcp" not in call[:2] for call in run.calls)


@pytest.mark.skipif(os.name == "nt", reason="POSIX tools layout")
def test_an_earlier_tools_install_is_reused_without_asking(tmp_path):
    run = PipRecorder(tmp_path)
    run.launcher.parent.mkdir(parents=True)
    run.launcher.write_text("")
    result = setup_provider(
        SPECS["jira"], JIRA_ENV, home=tmp_path, run=run, which=found("claude")
    )
    assert result.status == "registered"
    assert [call[0] for call in run.calls] == ["claude"]
