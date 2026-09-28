import sys
import tempfile
import unittest
from pathlib import Path

from meow.config import LintCommand
from meow.lint import make_lint_hook


def _write_script(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    return path


class LintHookTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.scripts_dir = Path(self._tmpdir.name)
        self.working_dir = Path.cwd()

    async def test_clean_file_returns_no_additional_context(self):
        script = _write_script(self.scripts_dir, "ok.py", "")
        command = LintCommand(command=f"{sys.executable} {script}")
        hook = make_lint_hook(self.working_dir, [command], timeout=5)

        result = await hook(
            {"tool_name": "Write", "tool_input": {"file_path": "foo.py"}},
            "tool-use-id",
            None,
        )

        self.assertEqual(result, {})

    async def test_failing_command_reports_output_as_additional_context(self):
        script = _write_script(
            self.scripts_dir, "fail.py", "raise SystemExit(1)\n"
        )
        command = LintCommand(command=f"{sys.executable} {script}")
        hook = make_lint_hook(self.working_dir, [command], timeout=5)

        result = await hook(
            {"tool_name": "Write", "tool_input": {"file_path": "foo.py"}},
            "tool-use-id",
            None,
        )

        self.assertIn(
            "Lint issues in foo.py",
            result["hookSpecificOutput"]["additionalContext"],
        )

    async def test_hanging_command_is_killed_after_timeout_instead_of_blocking(self):
        script = _write_script(
            self.scripts_dir, "hang.py", "import time\ntime.sleep(30)\n"
        )
        command = LintCommand(command=f"{sys.executable} {script}")
        hook = make_lint_hook(self.working_dir, [command], timeout=0.2)

        result = await hook(
            {"tool_name": "Write", "tool_input": {"file_path": "foo.py"}},
            "tool-use-id",
            None,
        )

        self.assertIn(
            "Timed out",
            result["hookSpecificOutput"]["additionalContext"],
        )

    async def test_non_write_edit_tool_is_ignored(self):
        script = _write_script(self.scripts_dir, "ok.py", "")
        command = LintCommand(command=f"{sys.executable} {script}")
        hook = make_lint_hook(self.working_dir, [command], timeout=5)

        result = await hook({"tool_name": "Read"}, "tool-use-id", None)

        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
