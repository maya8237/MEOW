import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from meow.agents.issue_fetcher import IssueFetcherAgent
from meow.project.config import LintCommand


class FakeProjectContext:
    def __init__(self, tmp_dir: Path):
        self.project_dir = tmp_dir
        self.repo_dir = tmp_dir
        self.config: dict = {"models": {"issue_fetcher": None}}

    def model(self, role):
        return self.config["models"][role]

    def active_working_dir(self):
        return self.project_dir

    @staticmethod
    def lint_commands():
        return [LintCommand(command="ruff check")]


def _result(subtype: str) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=0,
        duration_api_ms=0,
        is_error=subtype != "success",
        num_turns=1,
        session_id="session",
        result="done",
    )


class IssueFetcherCheckActiveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = FakeProjectContext(Path.cwd())
        self.agent = IssueFetcherAgent(self.context, {"command": "uvx", "args": []})

    async def test_passes_when_a_jira_tool_ran_and_result_succeeded(self):
        async def fake_query(*, prompt, options):
            await asyncio.sleep(0)
            yield AssistantMessage(
                content=[
                    ToolUseBlock(id="1", name="mcp__jira__jira_search", input={}),
                    TextBlock(text="JIRA_OK"),
                ],
                model="model",
            )
            yield _result("success")

        with patch("meow.agents.issue_fetcher.query", fake_query):
            await self.agent.check_active()  # should not raise

    async def test_raises_when_no_jira_tool_was_used(self):
        async def fake_query(*, prompt, options):
            await asyncio.sleep(0)
            yield AssistantMessage(content=[TextBlock(text="JIRA_OK")], model="model")
            yield _result("success")

        with (
            patch("meow.agents.issue_fetcher.query", fake_query),
            self.assertRaisesRegex(RuntimeError, "No active Jira MCP server"),
        ):
            await self.agent.check_active()

    async def test_raises_when_the_query_did_not_succeed(self):
        async def fake_query(*, prompt, options):
            await asyncio.sleep(0)
            yield AssistantMessage(
                content=[ToolUseBlock(id="1", name="mcp__jira__jira_search", input={})],
                model="model",
            )
            yield _result("error_during_execution")

        with (
            patch("meow.agents.issue_fetcher.query", fake_query),
            self.assertRaisesRegex(RuntimeError, "No active Jira MCP server"),
        ):
            await self.agent.check_active()


class IssueFetcherFetchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = FakeProjectContext(Path.cwd())
        self.agent = IssueFetcherAgent(self.context, {"command": "uvx", "args": []})

    async def test_raises_when_the_agent_never_wrote_the_output_file(self):
        async def fake_query(*, prompt, options):
            await asyncio.sleep(0)
            yield _result("success")

        with (
            patch("meow.agents.base.query", fake_query),
            self.assertRaisesRegex(RuntimeError, "did not write"),
        ):
            await self.agent.fetch("PROJ-1", "PROJ", Path("/tmp/does-not-exist.json"))
