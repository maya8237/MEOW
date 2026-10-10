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

from meow.agents.gitlab_fetcher import GitlabFetcherAgent
from meow.project.config_models import LintCommand


class FakeProjectContext:
    def __init__(self, tmp_dir: Path):
        self.project_dir = tmp_dir
        self.repo_dir = tmp_dir
        self.config: dict = {"models": {"gitlab_fetcher": None}}

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


class GitlabFetcherCheckActiveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = FakeProjectContext(Path.cwd())
        self.agent = GitlabFetcherAgent(self.context, {"command": "uvx", "args": []})

    async def test_passes_when_a_gitlab_tool_ran_and_result_succeeded(self):
        async def fake_query(*, prompt, options):
            await asyncio.sleep(0)
            yield AssistantMessage(
                content=[
                    ToolUseBlock(
                        id="1", name="mcp__gitlab__get_merge_request", input={}
                    ),
                    TextBlock(text="GITLAB_OK"),
                ],
                model="model",
            )
            yield _result("success")

        with patch("meow.agents.gitlab_fetcher.query", fake_query):
            await self.agent.check_active()  # should not raise

    async def test_raises_when_no_gitlab_tool_was_used(self):
        async def fake_query(*, prompt, options):
            await asyncio.sleep(0)
            yield AssistantMessage(content=[TextBlock(text="GITLAB_OK")], model="model")
            yield _result("success")

        with (
            patch("meow.agents.gitlab_fetcher.query", fake_query),
            self.assertRaisesRegex(RuntimeError, "No active GitLab MCP server"),
        ):
            await self.agent.check_active()

    async def test_raises_when_the_query_did_not_succeed(self):
        async def fake_query(*, prompt, options):
            await asyncio.sleep(0)
            yield AssistantMessage(
                content=[
                    ToolUseBlock(
                        id="1", name="mcp__gitlab__get_merge_request", input={}
                    )
                ],
                model="model",
            )
            yield _result("error_during_execution")

        with (
            patch("meow.agents.gitlab_fetcher.query", fake_query),
            self.assertRaisesRegex(RuntimeError, "No active GitLab MCP server"),
        ):
            await self.agent.check_active()


class GitlabFetcherFetchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = FakeProjectContext(Path.cwd())
        self.agent = GitlabFetcherAgent(self.context, {"command": "uvx", "args": []})

    async def test_raises_when_the_agent_never_wrote_the_output_file(self):
        async def fake_query(*, prompt, options):
            await asyncio.sleep(0)
            yield _result("success")

        with (
            patch("meow.agents.base.query", fake_query),
            self.assertRaisesRegex(RuntimeError, "did not write"),
        ):
            await self.agent.fetch(
                "https://gitlab.example.com/group/project/-/merge_requests/1",
                Path("/tmp/does-not-exist.json"),
            )


if __name__ == "__main__":
    unittest.main()
