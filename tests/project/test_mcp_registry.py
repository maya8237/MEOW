import json

import pytest

from meow.integrations.github_reviewer import _load_github_config
from meow.project.mcp_registry import (
    discover_provider_references,
    discover_registry,
    resolve_mcp,
)


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_scope_precedence(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "repo"
    root.mkdir()
    write(
        home / ".claude.json",
        {
            "mcpServers": {"x": {"command": "global"}},
            "projects": {str(root): {"mcpServers": {"x": {"command": "scoped"}}}},
        },
    )
    assert discover_registry(root, home=home)["x"]["command"] == "scoped"
    write(root / ".mcp.json", {"mcpServers": {"x": None}})
    assert discover_registry(root, home=home)["x"]["command"] == "scoped"
    write(home / ".claude.json", {"mcpServers": {"x": {"command": "global"}}})
    assert discover_registry(root, home=home)["x"] is None


def test_environment_is_current_and_errors_are_safe(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    write(
        tmp_path / ".mcp.json",
        {
            "mcpServers": {
                "x": {
                    "command": "uvx",
                    "args": ["${SECRET}"],
                    "env": {"TOKEN": "${SECRET}"},
                }
            }
        },
    )
    monkeypatch.setenv("SECRET", "first-secret")
    assert resolve_mcp({"from": "x"}, tmp_path)["env"]["TOKEN"] == "first-secret"
    monkeypatch.setenv("SECRET", "second-secret")
    assert resolve_mcp({"from": "x"}, tmp_path)["args"] == ["second-secret"]
    monkeypatch.delenv("SECRET")
    with pytest.raises(ValueError) as error:
        resolve_mcp({"from": "x"}, tmp_path)
    assert "secret" not in str(error.value).lower()


@pytest.mark.parametrize(
    "server",
    [
        None,
        {},
        {"command": "x", "args": [1]},
        {"command": "x", "env": {"T": 1}},
        {"type": "http", "url": "https://secret"},
        {"command": "x", "oauth": {"token": "secret"}},
    ],
)
def test_rejects_invalid_and_remote_without_leaking(tmp_path, server):
    with pytest.raises(ValueError) as error:
        resolve_mcp(server, tmp_path)
    assert "secret" not in str(error.value)


def test_malformed_and_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    (tmp_path / ".claude.json").write_text('{"token":"private",', encoding="utf-8")
    assert discover_registry(tmp_path) == {}
    with pytest.raises(ValueError, match="not found"):
        resolve_mcp({"from": "private"}, tmp_path)


def test_matching_and_ambiguity(tmp_path):
    write(
        tmp_path / ".mcp.json",
        {
            "mcpServers": {
                "issues": {"command": "uvx", "args": ["mcp-atlassian"]},
                "code": {"command": "github-mcp"},
                "remote": {"url": "https://gitlab.example/secret"},
            }
        },
    )
    result = discover_provider_references(tmp_path, home=tmp_path)
    assert result["references"] == {
        "jira": {"from": "issues"},
        "github": {"from": "code"},
    }
    assert "secret" not in str(result)
    write(tmp_path / ".claude.json", {"mcpServers": {"github-two": {"command": "x"}}})
    result = discover_provider_references(tmp_path, home=tmp_path)
    assert "github" not in result["references"]
    assert "Multiple" in result["hints"]["github"]


def test_provider_load_is_lazy_and_uses_launch_root(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    write(
        tmp_path / ".mcp.json",
        {"mcpServers": {"code": {"command": "npx", "args": ["github-mcp"]}}},
    )
    config = {
        "_project_root": str(tmp_path),
        "github": {"mcp": {"from": "code"}},
        "jira": {"mcp": {"from": "missing"}},
    }
    assert _load_github_config(config)["mcp"]["command"] == "npx"


def test_custom_config_dir_disabled_and_missing_runtime_environment(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("MISSING_TOKEN", raising=False)
    write(
        tmp_path / ".claude.json",
        {
            "mcpServers": {
                "github": {"command": "x", "env": {"TOKEN": "${MISSING_TOKEN}"}}
            }
        },
    )
    assert discover_provider_references(tmp_path)["references"]["github"] == {
        "from": "github"
    }
    with pytest.raises(ValueError, match="environment"):
        resolve_mcp({"from": "github"}, tmp_path)
    write(
        tmp_path / ".claude.json",
        {"projects": {str(tmp_path): {"disabledMcpjsonServers": ["github"]}}},
    )
    write(tmp_path / ".mcp.json", {"mcpServers": {"github": {"command": "x"}}})
    assert "github" not in discover_registry(tmp_path)
    assert discover_provider_references(tmp_path)["references"] == {}


def test_environment_default_and_oversized_file(tmp_path):
    assert resolve_mcp(
        {"command": "x", "args": ["${MEOW_ABSENT_123:-fallback}"]}, tmp_path
    )["args"] == ["fallback"]
    (tmp_path / ".mcp.json").write_text(" " * 2_000_001, encoding="utf-8")
    assert discover_registry(tmp_path, home=tmp_path) == {}
