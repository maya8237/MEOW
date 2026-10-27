import json
import subprocess
import tomllib

import pytest

from meow.cli.cli import cli_main
from meow.project.config import load_config
from meow.project.onboarding import LOCAL_CONFIG_RELPATH, onboard_project


@pytest.fixture
def project(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("Unexpected prompt"))
    return tmp_path


def run_onboard(project, *flags):
    with pytest.raises(SystemExit) as result:
        cli_main(["onboard", "--non-interactive", "--work-dir", str(project), *flags])
    return result.value.code


def test_base_setup_has_no_prompts_and_is_repeatable(project, capsys):
    assert run_onboard(project) == 0
    first = json.loads(capsys.readouterr().out)
    assert ".gitignore" in first["files"]
    assert "jira" in first["skipped"]
    assert not first["connection_tested"]
    assert run_onboard(project) == 0
    assert json.loads(capsys.readouterr().out)["files"] == []


def test_jira_flags_keep_credentials_local_and_reach_fetcher(project, capsys):
    from meow.integrations.issue_solver import _load_jira_config
    from meow.native.native import verify

    token = 'private-"token\\value'
    flags = [
        "--jira-project-key",
        "PROJ",
        "--jira-url",
        "https://jira.example",
        "--jira-username",
        "user@example.com",
        "--jira-token",
        token,
        "--jira-command",
        "uvx",
        "--jira-arg",
        "mcp-atlassian",
    ]
    assert run_onboard(project, *flags) == 0
    output = capsys.readouterr().out
    assert token not in output
    assert "jira" in json.loads(output)["configured"]
    local = project / LOCAL_CONFIG_RELPATH
    original = local.read_bytes()
    shared = (project / ".meow/config.toml").read_bytes()
    assert token not in shared.decode()
    config = load_config(project)
    assert _load_jira_config(config)["mcp"]["env"]["JIRA_API_TOKEN"] == token
    check = verify(project, run_lint=False)["integrations"]["jira"]["mcp"]
    assert "JIRA_URL" in check["environment_keys"]
    assert not check["required_environment_missing"]
    assert run_onboard(project, *flags) == 0
    assert local.read_bytes() == original
    assert (project / ".meow/config.toml").read_bytes() == shared


def test_optional_flags_preserve_comments_and_unrelated_values(project, capsys):
    assert onboard_project(project).error is None
    local = project / LOCAL_CONFIG_RELPATH
    local.write_text(
        '# keep this\nmax_rounds = 3 # rounds\n[models]\nreviewer = "haiku"\n'
    )
    assert (
        run_onboard(
            project,
            "--max-rounds",
            "5",
            "--gitlab-url",
            "https://gitlab.example",
            "--gitlab-token",
            "gitlab-secret",
            "--gitlab-command",
            "uvx",
            "--gitlab-arg",
            "mcp-gitlab",
            "--github-token",
            "$GITHUB_TOKEN",
            "--github-command",
            "github-mcp",
            "--github-arg=--stdio",
            "--set",
            'workflow.default="default"',
            "--write-tests",
        )
        == 0
    )
    text = local.read_text()
    assert "# keep this" in text and "# rounds" in text
    data = tomllib.loads(text)
    assert data["models"] == {"reviewer": "haiku"}
    assert data["max_rounds"] == int("5")
    assert data["github"]["mcp"]["args"] == ["--stdio"]
    assert (
        data["github"]["mcp"]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "$GITHUB_TOKEN"
    )
    assert data["tester"]["write_tests"] is True
    assert "gitlab-secret" not in capsys.readouterr().out


def test_credential_only_update_uses_shared_provider_settings(project, capsys):
    assert onboard_project(project).error is None
    shared = project / ".meow/config.toml"
    shared.write_text(
        '[jira]\nproject_key="PROJ"\n[jira.mcp]\n'
        'command="uvx"\nargs=["mcp-atlassian"]\n'
    )
    before = shared.read_bytes()
    assert run_onboard(project, "--jira-token", "$JIRA_API_TOKEN") == 0
    assert shared.read_bytes() == before
    local = project / LOCAL_CONFIG_RELPATH
    assert tomllib.loads(local.read_text())["jira"]["mcp"]["env"] == {
        "JIRA_API_TOKEN": "$JIRA_API_TOKEN"
    }
    result = subprocess.run(["git", "check-ignore", "-q", str(local)], cwd=project)
    assert result.returncode == 0


def test_config_file_and_set_support_optional_arrays(project, tmp_path, capsys):
    config = tmp_path / "options.toml"
    config.write_text('[models]\nreviewer="haiku"\n[tester]\nwrite_tests=true\n')
    assert (
        run_onboard(
            project,
            "--config",
            str(config),
            "--no-write-tests",
            "--test-command",
            "python -m pytest",
            "--build-command",
            "python -m build",
            "--set",
            'worktree_setup.copy=["assets"]',
            "--set",
            'models.reviewer="sonnet"',
        )
        == 0
    )
    data = tomllib.loads((project / LOCAL_CONFIG_RELPATH).read_text())
    assert data["models"]["reviewer"] == "sonnet"
    assert data["tester"]["write_tests"] is False
    assert data["tester"]["tests"] == [{"command": "python -m pytest"}]
    assert data["worktree_setup"]["copy"] == ["assets"]


@pytest.mark.parametrize(
    "flags",
    [
        ["--max-rounds", "0"],
        ["--jira-token", "secret"],
        ["--set", "github.mcp.args=12", "--github-command", "mcp"],
        ["--set", 'tester.tests="secret"'],
        ["--set", "delivery.target_branch=12"],
    ],
)
def test_invalid_settings_roll_back_and_do_not_echo_secrets(project, capsys, flags):
    assert onboard_project(project).error is None
    local = project / LOCAL_CONFIG_RELPATH
    before = local.read_bytes()
    assert run_onboard(project, *flags) == 1
    assert local.read_bytes() == before
    assert "secret" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "setting", ["models.reviewer=secret", 'x=1\ny="secret"', "=true"]
)
def test_malformed_set_fails_before_setup(project, capsys, setting):
    assert run_onboard(project, "--set", setting) == 1
    assert not (project / ".meow").exists()
    assert "secret" not in capsys.readouterr().out


def test_malformed_existing_toml_is_not_overwritten(project, capsys):
    local = project / LOCAL_CONFIG_RELPATH
    local.parent.mkdir()
    local.write_text('secret = "unfinished')
    before = local.read_bytes()
    assert run_onboard(project, "--max-rounds", "4") == 1
    assert local.read_bytes() == before
    assert "secret" not in capsys.readouterr().out
