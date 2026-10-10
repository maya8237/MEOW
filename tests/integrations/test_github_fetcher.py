import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_agent_sdk import (
    AssistantMessage,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from meow.agents.github_fetcher import GithubFetcherAgent
from meow.project.config_models import LintCommand


class FakeProjectContext:
    def __init__(self, tmp_dir: Path):
        self.project_dir = tmp_dir
        self.repo_dir = tmp_dir
        self.config: dict = {"models": {"github_fetcher": None}}

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


class GithubFetcherCheckActiveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = FakeProjectContext(Path.cwd())
        self.agent = GithubFetcherAgent(self.context, {"command": "uvx", "args": []})

    async def test_passes_when_a_github_tool_ran_and_result_succeeded(self):
        async def fake_query(*, prompt, options):
            await asyncio.sleep(0)
            yield AssistantMessage(
                content=[
                    ToolUseBlock(
                        id="1", name="mcp__github__get_pull_request", input={}
                    ),
                    TextBlock(text="GITHUB_OK"),
                ],
                model="model",
            )
            yield _result("success")

        with patch("meow.agents.github_fetcher.query", fake_query):
            await self.agent.check_active()

    async def test_raises_when_no_github_tool_was_used(self):
        async def fake_query(*, prompt, options):
            await asyncio.sleep(0)
            yield AssistantMessage(content=[TextBlock(text="GITHUB_OK")], model="model")
            yield _result("success")

        with (
            patch("meow.agents.github_fetcher.query", fake_query),
            self.assertRaisesRegex(RuntimeError, "No active GitHub MCP server"),
        ):
            await self.agent.check_active()

    async def test_raises_when_the_query_did_not_succeed(self):
        async def fake_query(*, prompt, options):
            await asyncio.sleep(0)
            yield AssistantMessage(
                content=[
                    ToolUseBlock(id="1", name="mcp__github__get_pull_request", input={})
                ],
                model="model",
            )
            yield _result("error_during_execution")

        with (
            patch("meow.agents.github_fetcher.query", fake_query),
            self.assertRaisesRegex(RuntimeError, "No active GitHub MCP server"),
        ):
            await self.agent.check_active()


class GithubFetcherFetchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = FakeProjectContext(Path.cwd())
        self.agent = GithubFetcherAgent(self.context, {"command": "uvx", "args": []})

    async def test_raises_when_the_agent_never_wrote_the_output_file(self):
        async def fake_query(*, prompt, options):
            await asyncio.sleep(0)
            yield _result("success")

        with (
            patch("meow.agents.base.query", fake_query),
            self.assertRaisesRegex(RuntimeError, "did not write"),
        ):
            await self.agent.fetch(
                "https://github.com/example/project/pull/1",
                Path("/tmp/does-not-exist.json"),
            )

    async def test_write_permission_is_scoped_to_the_output_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_file = Path(tmp) / "pull_request.json"
            captured = {}

            async def fake_query(*, prompt, options):
                captured["options"] = options
                output_file.write_text("{}", encoding="utf-8")
                await asyncio.sleep(0)
                yield _result("success")

            with patch("meow.agents.base.query", fake_query):
                await self.agent.fetch(
                    "https://github.com/example/project/pull/1", output_file
                )

            self.assertNotIn("Write", captured["options"].allowed_tools)
            callback = captured["options"].can_use_tool
            self.assertIsNotNone(callback)
            self.assertIsInstance(
                await callback("Write", {"file_path": str(output_file)}, None),
                PermissionResultAllow,
            )
            self.assertIsInstance(
                await callback(
                    "Write", {"file_path": str(Path(tmp) / "other.json")}, None
                ),
                PermissionResultDeny,
            )


if __name__ == "__main__":
    unittest.main()
