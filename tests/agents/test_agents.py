import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from claude_agent_sdk import AssistantMessage, ProcessError, ResultMessage, TextBlock

from meow.agents.base import Agent, AgentContext, ProjectContext, _looks_like_a_crash
from meow.agents.explorer import ExplorerAgent
from meow.agents.generator import Generator, GeneratorAgent
from meow.agents.lint_fixer import LintFixAgent
from meow.agents.planner import PlannerAgent
from meow.agents.review_fixer import ReviewFixAgent
from meow.agents.reviewer import (
    ReviewerAgent,
    _branch_diff,
    _run_git_retrying,
    _verdict_status,
)
from meow.infrastructure.lint import LintGateEvidence
from meow.project.config import LintCommand


class LooksLikeACrashTests(unittest.TestCase):
    def test_none_is_not_a_crash(self):
        self.assertFalse(_looks_like_a_crash(None))

    def test_normal_exit_codes_are_not_crashes(self):
        self.assertFalse(_looks_like_a_crash(0))
        self.assertFalse(_looks_like_a_crash(1))
        self.assertFalse(_looks_like_a_crash(127))

    def test_posix_signal_style_negative_code_is_a_crash(self):
        self.assertTrue(_looks_like_a_crash(-11))  # SIGSEGV

    def test_windows_fastfail_style_large_negative_code_is_a_crash(self):
        self.assertTrue(_looks_like_a_crash(-1073740791))  # sign-extended 0xC0000409

    def test_large_unsigned_ntstatus_style_code_is_a_crash(self):
        self.assertTrue(_looks_like_a_crash(3221226505))  # raw 0xC0000409


class ProjectContextTests(unittest.TestCase):
    def test_use_worktree_defaults_to_false(self):
        context = ProjectContext(Path("/repo"), {"models": {}, "lint": []})
        self.assertFalse(context.use_worktree)

    def test_use_worktree_can_be_set_true(self):
        context = ProjectContext(
            Path("/repo"), {"models": {}, "lint": []}, use_worktree=True
        )
        self.assertTrue(context.use_worktree)


class FakeProjectContext:
    """Generic agent context; intentionally unrelated to Sprint."""

    def __init__(self):
        self.model_calls = []
        self.project_dir = Path("/active/project")
        self.repo_dir = self.project_dir
        self.config: dict = {
            "docs_dir": "docs/exec-plans/active",
            "lint_timeout": 60,
        }
        self._lint_commands = [LintCommand(command="ruff check")]
        self.use_worktree = False

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

    async def test_run_query_retries_on_crash_and_succeeds(self):
        succeeds_on_attempt = 3
        calls = {"n": 0}
        success = ResultMessage(
            subtype="success",
            duration_ms=0,
            duration_api_ms=0,
            is_error=False,
            num_turns=1,
            session_id="session",
            result="done",
        )

        async def flaky_query(*, prompt, options):
            calls["n"] += 1
            await __import__("asyncio").sleep(0)
            if calls["n"] < succeeds_on_attempt:
                raise ProcessError(
                    "Command failed", exit_code=-1073740791, stderr="crashed"
                )
                yield  # pragma: no cover -- makes this an async generator
            yield success

        with (
            patch("meow.agents.base.query", flaky_query),
            patch("meow.agents.base.asyncio.sleep", new=AsyncMock()),
        ):
            await Agent.run_query("do work", object(), "Reviewer")

        self.assertEqual(calls["n"], succeeds_on_attempt)

    async def test_run_query_gives_up_after_max_retries_and_raises_clearly(self):
        calls = {"n": 0}

        async def always_crashes(*, prompt, options):
            calls["n"] += 1
            await __import__("asyncio").sleep(0)
            raise ProcessError(
                "Command failed", exit_code=-1073740791, stderr="crashed hard"
            )
            yield  # pragma: no cover -- makes this an async generator

        with (
            patch("meow.agents.base.query", always_crashes),
            patch("meow.agents.base.asyncio.sleep", new=AsyncMock()),
            self.assertRaisesRegex(
                RuntimeError, r"Reviewer.*crashed.*-1073740791.*crashed hard"
            ) as caught,
        ):
            await Agent.run_query("do work", object(), "Reviewer")

        self.assertIsInstance(caught.exception.__cause__, ProcessError)
        self.assertEqual(calls["n"], 3)

    async def test_run_query_does_not_retry_a_normal_process_error(self):
        calls = {"n": 0}

        async def normal_failure(*, prompt, options):
            calls["n"] += 1
            await __import__("asyncio").sleep(0)
            raise ProcessError("Command failed", exit_code=1, stderr="bad prompt")
            yield  # pragma: no cover -- makes this an async generator

        with (
            patch("meow.agents.base.query", normal_failure),
            self.assertRaisesRegex(RuntimeError, r"Reviewer.*exit code 1.*bad prompt"),
        ):
            await Agent.run_query("do work", object(), "Reviewer")

        self.assertEqual(calls["n"], 1)


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

    async def test_lint_fix_agent_keeps_one_client_and_uses_the_lint_hook(  # ruff: ignore[too-many-statements]
        self,
    ):
        client = MagicMock()
        client.__aenter__ = unittest.mock.AsyncMock(return_value=client)
        client.__aexit__ = unittest.mock.AsyncMock()
        client.query = unittest.mock.AsyncMock()

        async def responses():
            await asyncio.sleep(0)
            yield AssistantMessage(
                content=[TextBlock(text="fixed"), TextBlock(text=" it")],
                model="model",
            )

        client.receive_response.side_effect = [responses(), responses()]
        with patch(
            "meow.agents.lint_fixer.ClaudeSDKClient", return_value=client
        ) as sdk:
            async with LintFixAgent(self.context) as fixer:
                await fixer.fix("first batch")
                result = await fixer.fix("second batch")

        sdk.assert_called_once()
        options = sdk.call_args.kwargs["options"]
        self.assertEqual(options.model, "model-for-lint_fixer")
        self.assertEqual(options.cwd, str(self.context.project_dir))
        matcher = options.hooks["PostToolUse"][0]
        self.assertEqual(matcher.matcher, "Write|Edit")
        self.assertEqual(
            client.query.await_args_list[0].args,
            ("Fix these lint failures:\n\nfirst batch",),
        )
        self.assertEqual(
            client.query.await_args_list[1].args,
            ("Fix these lint failures:\n\nsecond batch",),
        )
        self.assertEqual(result, "fixed\n it")
        client.__aenter__.assert_awaited_once_with()
        client.__aexit__.assert_awaited_once()

    async def test_review_fix_agent_keeps_one_client_and_uses_the_lint_hook(  # ruff: ignore[too-many-statements]
        self,
    ):
        client = MagicMock()
        client.__aenter__ = unittest.mock.AsyncMock(return_value=client)
        client.__aexit__ = unittest.mock.AsyncMock()
        client.query = unittest.mock.AsyncMock()

        async def responses():
            await asyncio.sleep(0)
            yield AssistantMessage(
                content=[TextBlock(text="fixed"), TextBlock(text=" it")],
                model="model",
            )

        client.receive_response.side_effect = [responses(), responses()]
        with patch(
            "meow.agents.review_fixer.ClaudeSDKClient", return_value=client
        ) as sdk:
            async with ReviewFixAgent(self.context) as fixer:
                await fixer.fix("first batch")
                result = await fixer.fix("second batch")

        sdk.assert_called_once()
        options = sdk.call_args.kwargs["options"]
        self.assertEqual(options.model, "model-for-review_fixer")
        self.assertEqual(options.cwd, str(self.context.project_dir))
        matcher = options.hooks["PostToolUse"][0]
        self.assertEqual(matcher.matcher, "Write|Edit")
        self.assertEqual(
            client.query.await_args_list[0].args,
            ("Fix these review findings:\n\nfirst batch",),
        )
        self.assertEqual(
            client.query.await_args_list[1].args,
            ("Fix these review findings:\n\nsecond batch",),
        )
        self.assertEqual(result, "fixed\n it")
        client.__aenter__.assert_awaited_once_with()
        client.__aexit__.assert_awaited_once()


class ReviewerAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = FakeProjectContext()
        self.context.project_dir = Path.cwd()
        self.context.repo_dir = self.context.project_dir
        self.lint_patch = patch.object(
            ReviewerAgent,
            "_lint_evidence",
            new=AsyncMock(return_value=LintGateEvidence()),
        )
        self.lint_patch.start()
        self.addCleanup(self.lint_patch.stop)

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
        read_text.assert_called_once_with(encoding="utf-8")
        prompt, options, role = run_query.await_args.args
        self.assertIn(f"Review {plan_file}", prompt)
        self.assertIn("Harness lint evidence", prompt)
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

    async def test_blocking_lint_failure_overrides_reviewer_pass(self):
        plan_file = self.context.project_dir / "feature.md"
        verdict = "SUMMARY: reviewed\nSTATUS: PASS"
        self.lint_patch.stop()
        patcher = patch.object(
            ReviewerAgent,
            "_lint_evidence",
            new=AsyncMock(
                return_value=LintGateEvidence(blocking=("$ ruff check\\nfailed",))
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        with (
            patch.object(ReviewerAgent, "run_query", new_callable=AsyncMock),
            patch.object(Path, "read_text", return_value=verdict),
            patch.object(Path, "write_text") as write_text,
        ):
            status, received = await ReviewerAgent(self.context).review_plan(plan_file)
        self.assertEqual(status, "FAIL")
        self.assertIn("STATUS: FAIL", write_text.call_args.args[0])
        self.assertIn("Blocking lint failures", received)

    async def test_review_plan_includes_focus_text_when_given(self):
        plan_file = self.context.project_dir / "feature.md"
        verdict = "SUMMARY: reviewed\nSTATUS: PASS\ncriterion: PASS"

        with (
            patch.object(
                ReviewerAgent, "run_query", new_callable=AsyncMock
            ) as run_query,
            patch.object(Path, "read_text", return_value=verdict),
        ):
            await ReviewerAgent(self.context).review_plan(
                plan_file, focus="Check error handling on the API boundary"
            )

        _, options, _ = run_query.await_args.args
        self.assertIn("Check error handling on the API boundary", options.system_prompt)

    async def test_review_plan_omits_focus_text_by_default(self):
        plan_file = self.context.project_dir / "feature.md"
        verdict = "SUMMARY: reviewed\nSTATUS: PASS\ncriterion: PASS"

        with (
            patch.object(
                ReviewerAgent, "run_query", new_callable=AsyncMock
            ) as run_query,
            patch.object(Path, "read_text", return_value=verdict),
        ):
            await ReviewerAgent(self.context).review_plan(plan_file)

        _, options, _ = run_query.await_args.args
        self.assertNotIn("Pay particular attention to", options.system_prompt)

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
        read_text.assert_called_once_with(encoding="utf-8")
        prompt, options, role = run_query.await_args.args
        self.assertIn("Review the prompt: Ship the feature\n\ngit context", prompt)
        self.assertIn("Harness lint evidence", prompt)
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

    async def test_review_branch_computes_diff_and_preserves_branch_review_contract(
        self,
    ):
        verdict = "SUMMARY: reviewed\nSTATUS: PASS\nconcern: PASS"

        with (
            patch.object(
                ReviewerAgent, "run_query", new_callable=AsyncMock
            ) as run_query,
            patch(
                "meow.agents.reviewer._branch_diff", return_value="diff text"
            ) as branch_diff,
            patch.object(Path, "read_text", return_value=verdict),
        ):
            status, received_verdict = await ReviewerAgent(self.context).review_branch(
                "main", "feature/x"
            )

        self.assertEqual((status, received_verdict), ("PASS", verdict))
        branch_diff.assert_called_once_with(
            self.context.project_dir, "main", "feature/x"
        )
        prompt, options, role = run_query.await_args.args
        self.assertIn("diff text", prompt)
        self.assertEqual(role, "Reviewer")
        self.assertEqual(
            options.allowed_tools, ["Read", "Grep", "Glob", "Bash", "Write"]
        )
        self.assertIn("'main'", options.system_prompt)
        self.assertIn("'feature/x'", options.system_prompt)


class RunGitRetryingTests(unittest.TestCase):
    def test_retries_on_crash_like_returncode_then_succeeds(self):
        import subprocess

        responses = [
            subprocess.CompletedProcess(
                args=[], returncode=-1073740791, stdout="", stderr="crash"
            ),
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout="clean\n", stderr=""
            ),
        ]

        with (
            patch(
                "meow.agents.reviewer.subprocess.run", side_effect=responses
            ) as mock_run,
            patch("meow.agents.reviewer.time.sleep") as mock_sleep,
        ):
            result = _run_git_retrying(["git", "status"])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(mock_run.call_count, 2)
        mock_sleep.assert_called_once()

    def test_gives_up_after_max_attempts_and_returns_last_crashed_result(self):
        import subprocess

        crash = subprocess.CompletedProcess(
            args=[], returncode=-11, stdout="", stderr="killed"
        )

        with (
            patch(
                "meow.agents.reviewer.subprocess.run", return_value=crash
            ) as mock_run,
            patch("meow.agents.reviewer.time.sleep"),
        ):
            result = _run_git_retrying(["git", "status"])

        self.assertEqual(result.returncode, -11)
        self.assertEqual(mock_run.call_count, 3)

    def test_does_not_retry_a_normal_nonzero_exit(self):
        import subprocess

        normal_fail = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="not a repo"
        )

        with patch(
            "meow.agents.reviewer.subprocess.run", return_value=normal_fail
        ) as mock_run:
            result = _run_git_retrying(["git", "status"])

        self.assertEqual(result.returncode, 1)
        mock_run.assert_called_once()


class BranchDiffTests(unittest.TestCase):
    def test_diff_includes_committed_and_uncommitted_changes_since_merge_base(self):
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "t@example.com"], cwd=root, check=True
            )
            subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
            (root / "f.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
            subprocess.run(["git", "branch", "main"], cwd=root, check=True)
            subprocess.run(
                ["git", "checkout", "-q", "-b", "feature/x"], cwd=root, check=True
            )
            (root / "f.txt").write_text("committed change\n", encoding="utf-8")
            subprocess.run(
                ["git", "commit", "-q", "-am", "committed"], cwd=root, check=True
            )
            (root / "f.txt").write_text(
                "committed change\nuncommitted too\n", encoding="utf-8"
            )

            diff = _branch_diff(root, "main", "feature/x")

            self.assertIn("committed change", diff)
            self.assertIn("uncommitted too", diff)

    def test_raises_a_clear_error_when_target_does_not_exist(self):
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "t@example.com"], cwd=root, check=True
            )
            subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
            (root / "f.txt").write_text("x\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)

            with self.assertRaisesRegex(RuntimeError, "merge base.*exit code"):
                _branch_diff(root, "does-not-exist", "master")


if __name__ == "__main__":
    unittest.main()
