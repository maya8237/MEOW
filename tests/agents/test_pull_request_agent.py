"""The pull request role may only open a PR/MR and write its own output file,
and `allow_only_tools` denies by default."""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from claude_agent_sdk import (
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
)

from meow.agents.base import allow_only_tools
from meow.agents.orchestration import OrchestratorAgents, PullRequestAgent
from meow.project.config import DEFAULT_CONFIG
from meow.project.config_models import LintCommand
from meow.project.permissions import parse_policy

MCP = {"command": "uvx", "args": []}


class FakeProjectContext:
    def __init__(self, directory: Path):
        self.repo_dir = directory
        self.config: dict = {"models": {"pull_request": None}}
        self.use_worktree = True

    def model(self, role):
        return self.config["models"][role]

    def active_working_dir(self):
        return self.repo_dir

    @staticmethod
    def lint_commands():
        return [LintCommand(command="ruff check")]


def success() -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=0,
        duration_api_ms=0,
        is_error=False,
        num_turns=1,
        session_id="session",
        result="done",
    )


def run_open(agent, output_file, writer=None):
    """Run `agent.open` against a fake query; return (url, captured)."""
    captured = {}

    async def fake_query(*, prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options
        if writer is not None:
            output_file.write_text(writer, encoding="utf-8")
        await asyncio.sleep(0)
        yield success()

    async def call():
        return await agent.open(
            "Orchestrator: Add login", "Report body", "orch/abc", "main", output_file
        )

    with patch("meow.agents.base.query", fake_query):
        return asyncio.run(call()), captured


def allowed(options, tool, tool_input=None):
    result = asyncio.run(options.can_use_tool(tool, tool_input or {}, None))
    return isinstance(result, PermissionResultAllow)


def test_open_returns_the_url_the_agent_wrote(tmp_path):
    agent = PullRequestAgent(FakeProjectContext(tmp_path), MCP, "github")
    output = tmp_path / "pull-request.json"

    url, captured = run_open(
        agent, output, json.dumps({"url": "https://github.com/o/r/pull/7"})
    )

    assert url == "https://github.com/o/r/pull/7"
    assert "Title: Orchestrator: Add login" in captured["prompt"]
    assert "Report body" in captured["prompt"]
    assert "'orch/abc'" in captured["options"].system_prompt
    assert "pull request" in captured["options"].system_prompt


def test_gitlab_agent_speaks_of_merge_requests(tmp_path):
    agent = PullRequestAgent(FakeProjectContext(tmp_path), MCP, "gitlab")
    output = tmp_path / "pull-request.json"

    url, captured = run_open(
        agent,
        output,
        json.dumps({"url": "https://gitlab.example.com/g/r/-/merge_requests/3"}),
    )

    assert url.endswith("/merge_requests/3")
    assert "GitLab merge request" in captured["options"].system_prompt


def test_missing_output_file_is_an_error(tmp_path):
    agent = PullRequestAgent(FakeProjectContext(tmp_path), MCP, "github")

    with pytest.raises(RuntimeError, match="pull_request did not write"):
        run_open(agent, tmp_path / "pull-request.json")


@pytest.mark.parametrize(
    "payload", ['{"url": ""}', '{"link": "x"}', '{"url": "ftp://x"}']
)
def test_output_without_a_valid_url_is_an_error(tmp_path, payload):
    agent = PullRequestAgent(FakeProjectContext(tmp_path), MCP, "github")

    with pytest.raises(RuntimeError, match="no valid 'url'"):
        run_open(agent, tmp_path / "pull-request.json", payload)


def test_only_pull_request_tools_and_the_output_file_are_permitted(tmp_path):
    agent = PullRequestAgent(FakeProjectContext(tmp_path), MCP, "github")
    output = tmp_path / "pull-request.json"
    _url, captured = run_open(agent, output, '{"url": "https://x/1"}')
    options = captured["options"]

    assert "mcp__github__*" not in options.allowed_tools
    assert "Write" not in options.allowed_tools
    for tool in (
        "mcp__github__create_pull_request",
        "mcp__github__list_pull_requests",
        "mcp__github__search_pull_requests",
    ):
        assert allowed(options, tool)
    for tool in (
        "mcp__github__merge_pull_request",
        "mcp__github__update_pull_request",
        "mcp__gitlab__create_merge_request",
        "Bash",
        "Read",
    ):
        assert not allowed(options, tool)
    assert allowed(options, "Write", {"file_path": str(output)})
    assert not allowed(options, "Write", {"file_path": str(tmp_path / "other.json")})


def test_gitlab_permits_merge_request_tools_only(tmp_path):
    agent = PullRequestAgent(FakeProjectContext(tmp_path), MCP, "gitlab")
    _url, captured = run_open(
        agent, tmp_path / "pull-request.json", '{"url": "https://x/1"}'
    )
    options = captured["options"]

    assert allowed(options, "mcp__gitlab__create_merge_request")
    assert allowed(options, "mcp__gitlab__list_merge_requests")
    assert not allowed(options, "mcp__gitlab__merge_merge_request")


def test_allow_only_tools_denies_by_default_and_chains_the_previous_callback():
    async def previous(  # ruff: ignore[unused-async]
        tool, tool_input, context
    ):
        if tool == "Read" and tool_input.get("path") == "secret":
            return PermissionResultDeny(message="secret")
        return PermissionResultAllow()

    options = ClaudeAgentOptions(
        allowed_tools=["mcp__x__*", "Read"], can_use_tool=previous
    )

    allow_only_tools(options, lambda tool: tool == "Read", "only Read")

    assert options.allowed_tools == ["Read"]
    assert allowed(options, "Read", {"path": "ok"})
    assert not allowed(options, "Read", {"path": "secret"})
    denied = asyncio.run(options.can_use_tool("Bash", {}, None))
    assert isinstance(denied, PermissionResultDeny)
    assert denied.message == "only Read"


def test_allow_only_tools_without_a_previous_callback_allows_permitted_tools():
    options = ClaudeAgentOptions(allowed_tools=["Read"])

    allow_only_tools(options, lambda tool: tool == "Read", "only Read")

    assert allowed(options, "Read")
    assert not allowed(options, "Write")


def test_pull_request_is_wired_into_roles_models_and_agents():
    policy = parse_policy({
        "rule": [{"role": "pull_request", "tool": "Bash", "action": "deny"}]
    })

    assert [rule.tool for rule in policy.for_role("pull_request").rules] == ["Bash"]
    assert "pull_request" in DEFAULT_CONFIG["models"]
    assert OrchestratorAgents().pull_request is PullRequestAgent
