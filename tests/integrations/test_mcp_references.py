import asyncio
import json
import subprocess
from unittest.mock import AsyncMock

import pytest

from meow.agents.base import ProjectContext
from meow.agents.mcp_fetcher import IssueFetcherAgent
from meow.execution.orchestration.pull_request import detect_provider, provider_mcp
from meow.native import native
from meow.project.config import _merge_config, load_config
from meow.project.onboard_config import _validate_integrations


def test_loaded_config_and_delivery_use_launch_root(tmp_path, monkeypatch):
    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow/config.toml").write_text(
        '[github.mcp]\nfrom = "code"\n[jira.mcp]\nfrom = "absent"\n', encoding="utf-8"
    )
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"code": {"command": "stdio-launcher"}}}),
        encoding="utf-8",
    )
    config = load_config(tmp_path)
    assert config["jira"]["mcp"] == {"from": "absent"}
    monkeypatch.chdir(tmp_path.parent)
    assert provider_mcp(config, "github")["command"] == "stdio-launcher"


def test_native_reference_does_not_expose_launch_secrets(tmp_path, monkeypatch):
    (tmp_path / ".mcp.json").write_text(
        json.dumps({
            "mcpServers": {
                "code": {
                    "command": "secret-launcher",
                    "args": ["secret-arg"],
                    "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "secret-token"},
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(native.shutil, "which", lambda _: "available")
    config = {"_project_root": str(tmp_path), "github": {"mcp": {"from": "code"}}}
    result = native._verify_github(config)
    assert result["status"] == "ready_unchecked"
    assert "secret" not in json.dumps(result)
    config["github"]["mcp"]["from"] = "missing"
    assert native._verify_github(config)["status"] == "invalid_config"


def test_onboard_reference_is_structural_and_jira_key_optional():
    _validate_integrations({"jira": {"mcp": {"from": "not-yet-registered"}}}, {"jira"})
    _validate_integrations({"jira": {"mcp": {"command": "uvx"}}}, {"jira"})
    with pytest.raises(ValueError):
        _validate_integrations({"jira": {"mcp": {"from": ""}}}, {"jira"})


@pytest.mark.parametrize(
    "projects,expected",
    [
        ([{"key": "ONE"}], "ONE"),
        ([], None),
        ([{"key": "ONE"}, {"key": "TWO"}], None),
        ([{}], None),
    ],
)
def test_latest_issue_requires_unique_accessible_project(tmp_path, projects, expected):
    agent = IssueFetcherAgent(ProjectContext(tmp_path, {}), {"command": "x"})
    lookups = []

    async def fetch(lookup, fields, forbidden, output):  # ruff: ignore[unused-async] -- async fetcher contract
        lookups.append(lookup)
        output.write_text(json.dumps({"projects": projects}), encoding="utf-8")

    agent._fetch = fetch
    if expected:
        asyncio.run(agent.fetch(None, None, tmp_path / "issue.json"))
        assert "project = ONE" in lookups[-1]
    else:
        with pytest.raises(RuntimeError, match="project_key"):
            asyncio.run(agent.fetch(None, None, tmp_path / "issue.json"))
        assert len(lookups) == 1


def test_explicit_issue_never_lists_projects(tmp_path):
    agent = IssueFetcherAgent(ProjectContext(tmp_path, {}), {"command": "x"})
    agent.unique_project = AsyncMock(side_effect=AssertionError("must not list"))
    agent._fetch = AsyncMock()
    asyncio.run(agent.fetch("PROJ-1", None, tmp_path / "issue.json"))
    assert "PROJ-1" in agent._fetch.call_args.args[0]


def test_self_hosted_gitlab_reference(tmp_path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps({
            "mcpServers": {
                "code": {
                    "command": "x",
                    "env": {"GITLAB_URL": "https://code.company.example"},
                }
            }
        }),
        encoding="utf-8",
    )
    config = {"_project_root": str(tmp_path), "gitlab": {"mcp": {"from": "code"}}}
    assert detect_provider("git@code.company.example:team/repo.git", config) == "gitlab"


def test_pipeline_credentials_follow_references(tmp_path):
    from meow.integrations.pipeline_api import connect

    (tmp_path / ".mcp.json").write_text(
        json.dumps({
            "mcpServers": {
                "lab": {
                    "command": "x",
                    "env": {
                        "GITLAB_URL": "https://code.company.example",
                        "GITLAB_TOKEN": "lab-token",
                    },
                },
                "hub": {
                    "command": "x",
                    "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "hub-token"},
                },
            }
        }),
        encoding="utf-8",
    )
    config = {
        "_project_root": str(tmp_path),
        "gitlab": {"mcp": {"from": "lab"}},
        "github": {"mcp": {"from": "hub"}},
    }
    assert (
        connect("git@code.company.example:group/repo.git", config, {}).token
        == "lab-token"
    )
    assert connect("git@github.com:group/repo.git", config, {}).token == "hub-token"


def test_reference_and_inline_layers_replace_launch_definitions():
    inline = {"github": {"mcp": {"command": "x", "env": {"TOKEN": "old"}}}}
    reference = {"github": {"mcp": {"from": "code"}}}
    assert _merge_config(inline, reference) == reference
    assert _merge_config(reference, inline) == inline


@pytest.mark.parametrize(
    "previous",
    [{"from": "earlier-detected"}, {"command": "x", "env": {"TOKEN": "old"}}],
)
def test_empty_mcp_override_clears_lower_definition(previous):
    lower = {"jira": {"project_key": "KEEP", "mcp": previous}}
    result = _merge_config(lower, {"jira": {"mcp": {}}})
    assert result == {"jira": {"project_key": "KEEP", "mcp": {}}}
    assert lower["jira"]["mcp"] == previous


def test_local_empty_mcp_clears_previously_bound_project_reference(tmp_path):
    from meow.integrations.issue_solver import _load_jira_config

    (tmp_path / ".meow").mkdir()
    (tmp_path / ".meow/config.toml").write_text(
        '[jira]\nproject_key = "KEEP"\n[jira.mcp]\nfrom = "earlier-detected"\n',
        encoding="utf-8",
    )
    (tmp_path / ".meow/config.local.toml").write_text("[jira.mcp]\n", encoding="utf-8")
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"jira": {"command": "jira-mcp"}}}), encoding="utf-8"
    )
    config = load_config(tmp_path)
    assert config["jira"] == {"project_key": "KEEP", "mcp": {}}
    with pytest.raises(ValueError, match=r"\[jira.mcp\]"):
        _load_jira_config(config)


@pytest.mark.parametrize("section", [None, {}, {"mcp": {"from": "secret-name"}}])
def test_missing_provider_errors_offer_safe_reference_guidance(section, monkeypatch):
    from meow.project.mcp_registry import provider_mcp_config

    monkeypatch.setattr("meow.project.mcp_registry.discover_registry", lambda root: {})
    config = {} if section is None else {"jira": section}
    with pytest.raises(ValueError) as error:
        provider_mcp_config(config, "jira")
    message = str(error.value)
    assert '[jira.mcp] from = "registered-name"' in message
    assert "Claude MCP registration" in message
    assert "ambiguous" in message
    assert "secret-name" not in message


def test_onboard_cli_set_saves_reference_without_runtime_resolution(tmp_path, capsys):
    from meow.cli.cli import cli_main

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    with pytest.raises(SystemExit) as result:
        cli_main([
            "onboard",
            "--non-interactive",
            "--work-dir",
            str(tmp_path),
            "--set",
            'jira.mcp.from="not-registered-yet"',
        ])
    assert result.value.code == 0
    assert "not-registered-yet" not in capsys.readouterr().out
    assert load_config(tmp_path)["jira"]["mcp"] == {"from": "not-registered-yet"}


def test_onboard_cli_dotted_settings_switch_connection_and_keep_provider_keys(tmp_path):
    from meow.cli.cli import cli_main
    from meow.project.onboarding import onboard_project

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert onboard_project(tmp_path).error is None
    local = tmp_path / ".meow/config.local.toml"
    local.write_text(
        '[jira]\nbranch_prefix = "ticket/"\nproject_key = "KEEP"\n'
        '[jira.mcp]\ncommand = "old"\nargs = ["old-arg"]\n'
        '[jira.mcp.env]\nTOKEN = "superseded-secret"\n',
        encoding="utf-8",
    )
    for setting, expected in (
        ('jira.mcp.from="not-registered-yet"', {"from": "not-registered-yet"}),
        ('jira.mcp.command="replacement"', {"command": "replacement"}),
    ):
        with pytest.raises(SystemExit) as result:
            cli_main([
                "onboard",
                "--non-interactive",
                "--work-dir",
                str(tmp_path),
                "--set",
                setting,
            ])
        assert result.value.code == 0
        jira = load_config(tmp_path)["jira"]
        assert jira["mcp"] == expected
        assert jira["project_key"] == "KEEP"
        assert jira["branch_prefix"] == "ticket/"
        assert "superseded-secret" not in local.read_text(encoding="utf-8")


def test_explicit_target_retains_newly_detected_other_provider(tmp_path):
    from meow.cli.cli import cli_main

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"gitlab": {"command": "gitlab-mcp"}}}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as result:
        cli_main([
            "onboard",
            "--non-interactive",
            "--work-dir",
            str(tmp_path),
            "--target",
            "develop",
        ])
    assert result.value.code == 0
    config = load_config(tmp_path)
    assert config["gitlab"]["mcp"] == {"from": "gitlab"}
    assert config["delivery"]["target_branch"] == "develop"


@pytest.mark.parametrize("provider", ["github", "gitlab"])
def test_explicit_pipeline_token_never_resolves_unrelated_mcp(provider, monkeypatch):
    from meow.integrations.pipeline_api import connect

    monkeypatch.setattr(
        "meow.project.mcp_registry.discover_registry",
        lambda root: pytest.fail("Explicit pipeline token must bypass MCP resolution"),
    )
    config = {provider: {"mcp": {"from": "missing-or-unusable"}}}
    client = connect(
        f"git@{provider}.com:group/repo.git",
        config,
        {f"MEOW_{provider.upper()}_TOKEN": "explicit-token"},
    )
    assert client.token == "explicit-token"
