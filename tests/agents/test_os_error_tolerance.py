import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage

from meow.agents.base import Agent, guard_tools, prepare_output_file
from meow.infrastructure.lint import make_lint_hook
from meow.project.permissions import RolePolicy, Rule

FIRST_SUCCESS = 2


def _winerror(code=5):
    return PermissionError(13, "Access is denied", None, code)


class OsErrorToleranceTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_query_retries_an_os_error_then_succeeds(self):
        calls = {"n": 0}
        success = ResultMessage(
            subtype="success",
            duration_ms=0,
            duration_api_ms=0,
            is_error=False,
            num_turns=1,
            session_id="s",
            result="done",
        )

        async def flaky(*, prompt, options):
            calls["n"] += 1
            await asyncio.sleep(0)
            if calls["n"] < FIRST_SUCCESS:
                raise _winerror()
            yield success

        with (
            patch("meow.agents.base.query", flaky),
            patch("meow.agents.base.asyncio.sleep", new=AsyncMock()),
        ):
            await Agent.run_query("x", object(), "Generator")
        self.assertEqual(calls["n"], FIRST_SUCCESS)

    async def test_run_query_raises_after_persistent_os_errors(self):
        async def broken(*, prompt, options):
            await asyncio.sleep(0)
            raise _winerror()
            yield  # pragma: no cover

        with (
            patch("meow.agents.base.query", broken),
            patch("meow.agents.base.asyncio.sleep", new=AsyncMock()),
            self.assertRaises(OSError),
        ):
            await Agent.run_query("x", object(), "Generator")

    async def test_guard_denies_instead_of_crashing_on_os_error(self):
        options = ClaudeAgentOptions(allowed_tools=["Write"])

        def boom(_input):
            raise _winerror()

        guard_tools(options, {"Write"}, boom)
        result = await options.can_use_tool("Write", {}, None)
        self.assertEqual(type(result).__name__, "PermissionResultDeny")

    async def test_lint_hook_swallows_os_error(self):
        hook = make_lint_hook(Path("."), [])
        with patch(
            "meow.infrastructure.lint.run_lint_on_file", side_effect=_winerror()
        ):
            out = await hook(
                {"tool_name": "Write", "tool_input": {"file_path": "a.py"}}, None, None
            )
        self.assertEqual(out, {})


class SyncToleranceTests(unittest.TestCase):
    def test_prepare_output_file_survives_a_locked_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.json"
            target.write_text("stale", encoding="utf-8")
            with (
                patch.object(Path, "unlink", side_effect=_winerror(32)),
                patch("meow.agents.base.time.sleep"),
            ):
                prepare_output_file(target)
            self.assertEqual(target.read_text(encoding="utf-8"), "")

    def test_permission_decision_denies_when_path_cannot_resolve(self):
        policy = RolePolicy((Rule("Generator", "Write", "deny", "secrets"),))
        with patch.object(Path, "resolve", side_effect=_winerror(123)):
            decision = policy.decision(
                "Write", {"file_path": "a.py"}, project_root=Path("/p")
            )
        self.assertEqual(decision, "deny")
