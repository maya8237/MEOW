"""[mcp_setup.NAME] overrides come only from user and local config."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from meow.cli.cli import cli_main
from meow.project.mcp_setup import build_command, setup_providers
from meow.project.mcp_setup_config import load_specs

SECRET = "s3cret-token"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(argv)
        return SimpleNamespace(returncode=0)


def found(*names):
    return lambda name: f"/bin/{name}" if name in names else None


def test_user_config_pins_the_package_version(tmp_path):
    write(
        tmp_path / ".meow/config.toml",
        '[mcp_setup.gitlab]\nargs = ["-y", "@zereight/mcp-gitlab@2.1.45"]\n',
    )
    specs, notes = load_specs(home=tmp_path)
    assert specs["gitlab"].args == ("-y", "@zereight/mcp-gitlab@2.1.45")
    assert specs["gitlab"].launcher == "npx"  # untouched fields keep defaults
    assert specs["gitlab"].custom and not specs["jira"].custom
    assert notes == []


def test_local_config_wins_over_user_config(tmp_path):
    home, repo = tmp_path / "home", tmp_path / "repo"
    write(home / ".meow/config.toml", '[mcp_setup.jira]\ncommand = "pipx"\n')
    write(repo / ".meow/config.local.toml", '[mcp_setup.jira]\ncommand = "uv"\n')
    specs, _ = load_specs(repo, home=home)
    assert specs["jira"].launcher == "uv"


def test_shared_project_config_cannot_change_setup(tmp_path):
    home, repo = tmp_path / "home", tmp_path / "repo"
    write(
        repo / ".meow/config.toml",
        '[mcp_setup.jira]\ncommand = "curl"\nargs = ["evil.example"]\n',
    )
    specs, notes = load_specs(repo, home=home)
    assert specs["jira"].launcher == "uvx"
    assert len(notes) == 1
    assert "shared .meow/config.toml" in notes[0]


def test_a_new_provider_is_registered_with_its_own_variables(tmp_path):
    write(
        tmp_path / ".meow/config.toml",
        """[mcp_setup.github]
command = "docker"
args = ["run", "-i", "--rm", "-e", "GITHUB_PERSONAL_ACCESS_TOKEN", "my/image"]
label = "GitHub"
auth = [["GITHUB_PERSONAL_ACCESS_TOKEN"]]
warmup = ["docker", "pull", "my/image"]
[mcp_setup.github.env]
GITHUB_HOST = "https://gh.example.com"
""",
    )
    specs, _ = load_specs(home=tmp_path)
    run = Recorder()
    [result] = setup_providers(
        ["github"],
        {"GITHUB_PERSONAL_ACCESS_TOKEN": SECRET},
        specs=specs,
        home=tmp_path,
        run=run,
        which=found("claude", "docker"),
    )
    assert result.status == "registered"
    assert "configured command" in result.detail
    assert SECRET not in json.dumps(result.to_dict())
    add = run.calls[0]
    assert add[:6] == ["claude", "mcp", "add", "--scope", "user", "github"]
    assert "GITHUB_HOST=https://gh.example.com" in add
    assert f"GITHUB_PERSONAL_ACCESS_TOKEN={SECRET}" in add
    assert add[add.index("--") + 1 :][:2] == ["docker", "run"]
    assert run.calls[1] == ["docker", "pull", "my/image"]


def test_empty_warmup_disables_the_download(tmp_path):
    write(tmp_path / ".meow/config.toml", "[mcp_setup.jira]\nwarmup = []\n")
    specs, _ = load_specs(home=tmp_path)
    run = Recorder()
    [result] = setup_providers(
        ["jira"],
        {"JIRA_URL": "u", "JIRA_PERSONAL_TOKEN": SECRET},
        specs=specs,
        home=tmp_path,
        run=run,
        which=found("claude", "uvx"),
    )
    assert result.status == "registered"
    assert len(run.calls) == 1


def test_a_custom_server_name_is_detected_as_already_registered(tmp_path):
    write(tmp_path / ".meow/config.toml", '[mcp_setup.jira]\nserver = "my-jira"\n')
    (tmp_path / ".claude.json").write_text(
        json.dumps({"mcpServers": {"my-jira": {"command": "x"}}})
    )
    specs, _ = load_specs(home=tmp_path)
    run = Recorder()
    [result] = setup_providers(
        ["jira"], {}, specs=specs, home=tmp_path, run=run, which=found("claude")
    )
    assert result.status == "already_registered"
    assert run.calls == []


@pytest.mark.parametrize(
    "text",
    [
        "[mcp_setup.jira]\nunknown = 1\n",
        '[mcp_setup.jira]\nargs = "uvx"\n',
        '[mcp_setup.jira]\nauth = [["not a name"]]\n',
        '[mcp_setup.jira.env]\nJIRA_API_TOKEN = "' + SECRET + '"\n',
        '[mcp_setup.brand-new]\nargs = ["x"]\n',
        '[mcp_setup.Bad_Name]\ncommand = "x"\n',
        '[mcp_setup.jira]\ncommand = "--flag"\n',
        "mcp_setup = 5\n",
    ],
)
def test_invalid_overrides_fail_without_quoting_values(tmp_path, text):
    write(tmp_path / ".meow/config.toml", text)
    with pytest.raises(ValueError) as error:
        load_specs(home=tmp_path)
    assert SECRET not in str(error.value)


def test_unreadable_config_is_reported_by_path(tmp_path):
    write(tmp_path / ".meow/config.toml", "[mcp_setup\n")
    with pytest.raises(ValueError, match="cannot read"):
        load_specs(home=tmp_path)


def test_build_command_puts_fixed_settings_before_credentials(tmp_path):
    write(
        tmp_path / ".meow/config.toml",
        '[mcp_setup.jira.env]\nJIRA_SSL_VERIFY = "false"\n',
    )
    specs, _ = load_specs(home=tmp_path)
    add = build_command(specs["jira"], {"JIRA_URL": "u"})
    assert add.index("JIRA_SSL_VERIFY=false") < add.index("JIRA_URL=u")


def test_onboard_reports_an_invalid_override_as_a_failed_result(
    tmp_path, monkeypatch, capsys
):
    home, repo = tmp_path / "home", tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for name in ("HOME", "USERPROFILE"):  # Path.home() on Windows
        monkeypatch.setenv(name, str(home))
    write(home / ".meow/config.toml", "[mcp_setup.jira]\nunknown = 1\n")
    with pytest.raises(SystemExit) as result:
        cli_main([
            "onboard",
            "--non-interactive",
            "--work-dir",
            str(repo),
            "--mcp",
            "jira",
        ])
    assert result.value.code == 1
    [item] = json.loads(capsys.readouterr().out)["mcp"]
    assert item["status"] == "failed"
    assert "unknown key" in item["detail"]


def test_onboard_reports_ignored_shared_setup_config(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for name in ("HOME", "USERPROFILE"):  # Path.home() on Windows
        monkeypatch.setenv(name, str(tmp_path / "home"))
    write(repo / ".meow/config.toml", '[mcp_setup.jira]\ncommand = "curl"\n')
    monkeypatch.setattr("meow.cli.onboard_cli.setup_providers", lambda *a, **k: [])
    with pytest.raises(SystemExit):
        cli_main([
            "onboard",
            "--non-interactive",
            "--work-dir",
            str(repo),
            "--mcp",
            "jira",
        ])
    assert (
        "shared .meow/config.toml"
        in json.loads(capsys.readouterr().out)["mcp_notes"][0]
    )


def test_installer_stops_on_an_invalid_override(tmp_path, monkeypatch, capsys):
    from meow.installer import _runtime

    for name in ("HOME", "USERPROFILE"):  # Path.home() on Windows
        monkeypatch.setenv(name, str(tmp_path))
    write(tmp_path / ".meow/config.toml", "[mcp_setup.jira]\nunknown = 1\n")
    lines = []
    assert _runtime._install_mcp("jira", lines.append) == 1
    assert "unknown key" in lines[0]
    _runtime._offer_mcp(lambda _prompt: "y", lines.append)
    assert "MCP setup skipped" in lines[-1]
