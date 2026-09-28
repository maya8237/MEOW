import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from meow.agents.base import Agent, AgentContext
from meow.agents.explorer import ExplorerAgent
from meow.agents.generator import Generator, GeneratorAgent
from meow.agents.planner import PlannerAgent
from meow.agents.reviewer import ReviewerAgent, _verdict_status
from meow.config import LintCommand


class FakeProjectContext:
    """Generic agent context; intentionally unrelated to Sprint."""

    def __init__(self):
        self.model_calls = []
        self.project_dir = Path("/active/project")
        self.repo_dir = self.project_dir
        self.config: dict = {"docs_dir": "docs/exec-plans/active"}
        self._lint_commands = [LintCommand(command="ruff check")]

    def model(self, role):
        self.model_calls.append(role)
        return f"model-for-{role}"

    def active_working_dir(self):
        return self.project_dir

    def lint_commands(self):
        return self._lint_commands


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context: AgentContext = FakeProjectContext()
        self.agent = Agent(self.context)

    def test_options_use_role_model_active_directory_and_forward_options(self):
        options = self.agent.options(
            system_prompt="instructions",
            allowed_tools=["Read", "Write"],
            role="planner",
            agents={"explorer": "definition"},
            setting_sources=["project"],
        )

        self.assertEqual(self.context.model_calls, ["planner"])
        self.assertEqual(options.system_prompt, "instructions")
        self.assertEqual(options.allowed_tools, ["Read", "Write"])
        self.assertEqual(options.model, "model-for-planner")
        self.assertEqual(options.cwd, str(self.context.project_dir))
        self.assertEqual(options.agents, {"explorer": "definition"})
        self.assertEqual(options.setting_sources, ["project"])

    async def test_run_query_raises_role_specific_error_on_failed_result(self):
        failure = ResultMessage(
            subtype="error_during_execution",
            duration_ms=0,
            duration_api_ms=0,
            is_error=True,
            num_turns=1,
            session_id="session",
            result="failed",
        )

        async def fake_query(*, prompt, options):
            await __import__("asyncio").sleep(0)
            yield failure

        with (
            patch("meow.agents.base.query", fake_query),
            self.assertRaisesRegex(
                RuntimeError, "Planner failed: error_during_execution"
            ),
        ):
            await Agent.run_query("do work", object(), "Planner")

    @staticmethod
    async def test_run_query_accepts_success_result():
        success = ResultMessage(
            subtype="success",
            duration_ms=0,
            duration_api_ms=0,
            is_error=False,
            num_turns=1,
            session_id="session",
            result="done",
        )

        async def fake_query(*, prompt, options):
            await __import__("asyncio").sleep(0)
            yield success

        with patch("meow.agents.base.query", fake_query):
            await Agent.run_query("do work", object(), "Planner")


class RoleAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = FakeProjectContext()
        self.context.project_dir = Path.cwd()

    def test_explorer_definition_preserves_role_configuration(self):
        definition = ExplorerAgent(self.context).definition()

        self.assertEqual(
            definition.description,
            "Read-only codebase/log/test-output exploration. Use for any "
            "research whose raw output doesn't need to be kept in full.",
        )
        self.assertEqual(
            definition.prompt,
            "You are a read-only research agent. Investigate the question "
            f"you're given within this project directory: {self.context.project_dir}. "
            "Treat it as the project root and resolve relative paths from "
            "it. Then return only a concise summary with "
            "file:line references -- never dump raw file contents or full "
            "command output unless specifically asked to. When the task is "
            "about a bug, use systematic debugging to gather evidence and "
            "trace likely causes; stay read-only and report findings.",
        )
        self.assertEqual(definition.tools, ["Read", "Grep", "Glob", "Bash"])
        self.assertEqual(definition.model, "model-for-explorer")

    async def test_planner_writes_plan_with_context_options_and_explorer(self):
        observed = {}

        async def fake_query(*, prompt, options):
            observed["prompt"] = prompt
            observed["options"] = options
            await __import__("asyncio").sleep(0)
            yield ResultMessage(
                subtype="success",
                duration_ms=0,
                duration_api_ms=0,
                is_error=False,
                num_turns=1,
                session_id="session",
                result="done",
            )

        with patch("meow.agents.base.query", fake_query):
            plan_file = await PlannerAgent(self.context).run(
                "ship-it", "Add CSV export"
            )

        expected_dir = self.context.project_dir / self.context.config["docs_dir"]
        self.assertEqual(plan_file, expected_dir / "ship-it.md")
        self.assertTrue(plan_file.parent.is_dir())
        self.assertEqual(observed["prompt"], "Add CSV export")
        options = observed["options"]
        self.assertEqual(options.model, "model-for-planner")
        self.assertEqual(options.cwd, str(self.context.project_dir))
        self.assertEqual(options.agents["explorer"].model, "model-for-explorer")

    async def test_planner_raises_on_sdk_failure(self):
        failure = ResultMessage(
            subtype="error_during_execution",
            duration_ms=0,
            duration_api_ms=0,
            is_error=True,
            num_turns=1,
            session_id="session",
            result="failed",
        )

        async def fake_query(*, prompt, options):
            await __import__("asyncio").sleep(0)
            yield failure

        with (
            patch("meow.agents.base.query", fake_query),
            self.assertRaisesRegex(
                RuntimeError, "Planner failed: error_during_execution"
            ),
        ):
            await PlannerAgent(self.context).run(None, "Build a plan")

    async def test_generator_keeps_one_client_and_uses_context_configuration(self):  # ruff: ignore[too-many-statements]
        self.context.explorer = object()
        self.context.lint_hook = object()
        plan_file = self.context.project_dir / "plan.md"
        client = MagicMock()
        client.__aenter__ = unittest.mock.AsyncMock(return_value=client)
        client.__aexit__ = unittest.mock.AsyncMock()
        client.query = unittest.mock.AsyncMock()

        async def responses():
            await asyncio.sleep(0)
            yield AssistantMessage(
                content=[TextBlock(text="first"), TextBlock(text=" response")],
                model="model",
            )

        client.receive_response.side_effect = [responses(), responses()]
        with patch("meow.agents.generator.ClaudeSDKClient", return_value=client) as sdk:
            async with GeneratorAgent(self.context, plan_file) as generator:
                await generator.implement("first instruction")
                result = await generator.implement("second instruction")

        sdk.assert_called_once()
        options = sdk.call_args.kwargs["options"]
        self.assertEqual(options.model, "model-for-generator")
        self.assertEqual(options.cwd, str(self.context.project_dir))
        self.assertEqual(options.agents, {"explorer": self.context.explorer})
        matcher = options.hooks["PostToolUse"][0]
        self.assertEqual(matcher.matcher, "Write|Edit")
        self.assertEqual(matcher.hooks, [self.context.lint_hook])
        self.assertEqual(client.query.await_args_list[0].args, ("first instruction",))
        self.assertEqual(client.query.await_args_list[1].args, ("second instruction",))
        self.assertEqual(result, "first\n response")
        client.__aenter__.assert_awaited_once_with()
        client.__aexit__.assert_awaited_once()
        self.assertIs(Generator, GeneratorAgent)


class ReviewerAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = FakeProjectContext()
        self.context.project_dir = Path.cwd()
        self.context.repo_dir = self.context.project_dir

    def test_verdict_status_accepts_pass_fail_and_defaults_missing_status_to_fail(self):
        self.assertEqual(_verdict_status("SUMMARY: good\nSTATUS: PASS"), "PASS")
        self.assertEqual(_verdict_status("SUMMARY: bad\nSTATUS: FAIL"), "FAIL")
        self.assertEqual(_verdict_status("SUMMARY: inconclusive"), "FAIL")

    async def test_review_plan_uses_generic_context_and_preserves_plan_review_contract(
        self,
    ):
        plan_file = self.context.project_dir / "feature.md"
        review_file = self.context.project_dir / "feature-review.md"
        verdict = "SUMMARY: reviewed\nSTATUS: PASS\ncriterion: PASS"

        with (
            patch.object(
                ReviewerAgent, "run_query", new_callable=AsyncMock
            ) as run_query,
            patch.object(Path, "read_text", return_value=verdict) as read_text,
        ):
            status, received_verdict = await ReviewerAgent(self.context).review_plan(
                plan_file
            )

        self.assertEqual((status, received_verdict), ("PASS", verdict))
        read_text.assert_called_once_with()
        prompt, options, role = run_query.await_args.args
        self.assertEqual(prompt, f"Review {plan_file}")
        self.assertEqual(role, "Reviewer")
        self.assertEqual(options.cwd, str(self.context.project_dir))
        self.assertEqual(options.model, "model-for-reviewer")
        self.assertEqual(
            options.allowed_tools, ["Read", "Grep", "Glob", "Bash", "Write"]
        )
        self.assertIn(f"Read the Sprint Contract in {plan_file}", options.system_prompt)
        self.assertIn(f"Write your verdict to {review_file}", options.system_prompt)
        self.assertIn("ruff check", options.system_prompt)
        self.assertIn("SOLID/SRP", options.system_prompt)

    async def test_review_prompt_uses_generic_context_git_review_and_docs_review_file(
        self,
    ):
        review_file = (
            self.context.project_dir / self.context.config["docs_dir"] / "review.md"
        )
        verdict = "SUMMARY: reviewed\nSTATUS: FAIL\nrequirement: FAIL"

        with (
            patch(
                "meow.agents.reviewer._git_review_context",
                return_value=("git context", True),
            ) as git_context,
            patch.object(
                ReviewerAgent, "run_query", new_callable=AsyncMock
            ) as run_query,
            patch.object(Path, "read_text", return_value=verdict) as read_text,
        ):
            status, received_verdict = await ReviewerAgent(self.context).review_prompt(
                "Ship the feature"
            )

        self.assertEqual((status, received_verdict), ("FAIL", verdict))
        git_context.assert_called_once_with(self.context)
        read_text.assert_called_once_with()
        prompt, options, role = run_query.await_args.args
        self.assertEqual(prompt, "Review the prompt: Ship the feature\n\ngit context")
        self.assertEqual(role, "Reviewer")
        self.assertEqual(options.cwd, str(self.context.project_dir))
        self.assertEqual(options.model, "model-for-reviewer")
        self.assertEqual(
            options.allowed_tools, ["Read", "Grep", "Glob", "Bash", "Write"]
        )
        self.assertIn("There is no Sprint Contract", options.system_prompt)
        self.assertIn(f"Write your verdict to {review_file}", options.system_prompt)
        self.assertIn("ruff check", options.system_prompt)
        self.assertIn("SOLID/SRP", options.system_prompt)


if __name__ == "__main__":
    unittest.main()
