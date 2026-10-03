import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from meow import lint_fix


class RunLintFixReportOnlyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    async def test_returns_none_when_lint_is_clean(self):
        working_dir = Path(self.temp.name)
        config = {"lint": [], "lint_timeout": 60, "models": {}}

        with (
            patch("meow.infrastructure.lint_fix.load_config", return_value=config),
            patch(
                "meow.infrastructure.lint_fix.check_lint_commands", new=AsyncMock(return_value=[])
            ) as mock_check,
            patch("meow.infrastructure.lint_fix.apply_lint_fixes", new=AsyncMock()) as mock_apply,
        ):
            result = await lint_fix.run_lint_fix(working_dir, report_only=True)

        self.assertIsNone(result)
        mock_check.assert_awaited_once_with(working_dir, config["lint"], 60)
        mock_apply.assert_not_awaited()

    async def test_returns_the_raw_problems_without_fixing_anything(self):
        working_dir = Path(self.temp.name)
        config = {"lint": [], "lint_timeout": 60, "models": {}}
        problems = ["$ ruff check\nfoo.py:1: F401 unused import"]

        with (
            patch("meow.infrastructure.lint_fix.load_config", return_value=config),
            patch(
                "meow.infrastructure.lint_fix.check_lint_commands",
                new=AsyncMock(return_value=problems),
            ),
            patch("meow.infrastructure.lint_fix.apply_lint_fixes", new=AsyncMock()) as mock_apply,
            patch("meow.infrastructure.lint_fix.LintFixAgent") as mock_agent_cls,
        ):
            result = await lint_fix.run_lint_fix(working_dir, report_only=True)

        self.assertEqual(result, problems[0])
        mock_apply.assert_not_awaited()
        mock_agent_cls.assert_not_called()


class RunLintFixStandaloneTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    async def test_returns_none_without_an_agent_when_the_fix_pass_is_enough(self):
        working_dir = Path(self.temp.name)
        config = {"lint": [], "lint_timeout": 60, "models": {}, "max_rounds": 3}

        with (
            patch("meow.infrastructure.lint_fix.load_config", return_value=config),
            patch("meow.infrastructure.lint_fix.apply_lint_fixes", new=AsyncMock()) as mock_apply,
            patch(
                "meow.infrastructure.lint_fix.check_lint_commands", new=AsyncMock(return_value=[])
            ) as mock_check,
            patch("meow.infrastructure.lint_fix.LintFixAgent") as mock_agent_cls,
        ):
            result = await lint_fix.run_lint_fix(working_dir, report_only=False)

        self.assertIsNone(result)
        mock_apply.assert_awaited_once_with(working_dir, config["lint"], 60)
        mock_check.assert_awaited_once_with(working_dir, config["lint"], 60)
        mock_agent_cls.assert_not_called()

    async def test_loops_the_fixer_agent_until_lint_is_clean(self):
        working_dir = Path(self.temp.name)
        config = {"lint": [], "lint_timeout": 60, "models": {}, "max_rounds": 3}

        mock_fixer = AsyncMock()
        mock_fixer.__aenter__ = AsyncMock(return_value=mock_fixer)
        mock_fixer.__aexit__ = AsyncMock(return_value=False)
        mock_fixer.fix = AsyncMock(return_value="")

        with (
            patch("meow.infrastructure.lint_fix.load_config", return_value=config),
            patch("meow.infrastructure.lint_fix.apply_lint_fixes", new=AsyncMock()),
            patch(
                "meow.infrastructure.lint_fix.check_lint_commands",
                new=AsyncMock(side_effect=[["still broken"], []]),
            ) as mock_check,
            patch("meow.infrastructure.lint_fix.LintFixAgent", return_value=mock_fixer),
        ):
            result = await lint_fix.run_lint_fix(working_dir, report_only=False)

        self.assertIsNone(result)
        self.assertEqual(mock_check.await_count, 2)
        mock_fixer.fix.assert_awaited_once_with("still broken")

    async def test_raises_lint_fix_error_after_max_rounds_still_failing(self):
        working_dir = Path(self.temp.name)
        config = {"lint": [], "lint_timeout": 60, "models": {}, "max_rounds": 2}

        mock_fixer = AsyncMock()
        mock_fixer.__aenter__ = AsyncMock(return_value=mock_fixer)
        mock_fixer.__aexit__ = AsyncMock(return_value=False)
        mock_fixer.fix = AsyncMock(return_value="")

        with (
            patch("meow.infrastructure.lint_fix.load_config", return_value=config),
            patch("meow.infrastructure.lint_fix.apply_lint_fixes", new=AsyncMock()),
            patch(
                "meow.infrastructure.lint_fix.check_lint_commands",
                new=AsyncMock(return_value=["still broken"]),
            ),
            patch("meow.infrastructure.lint_fix.LintFixAgent", return_value=mock_fixer),
            self.assertRaisesRegex(lint_fix.LintFixError, "still broken"),
        ):
            await lint_fix.run_lint_fix(working_dir, report_only=False)

        self.assertEqual(mock_fixer.fix.await_count, 2)


if __name__ == "__main__":
    unittest.main()
