import sys
import tempfile
import unittest
from pathlib import Path

from meow.config import LintCommand
from meow.lint import apply_lint_fixes, check_lint_commands, make_lint_hook


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


class ProjectWideLintTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.scripts_dir = Path(self._tmpdir.name)
        self.working_dir = Path.cwd()

    async def test_apply_lint_fixes_appends_the_configured_fix_flag(self):
        marker = self.scripts_dir / "argv.txt"
        script = _write_script(
            self.scripts_dir,
            "record_argv.py",
            "import sys\n"
            f"open(r'{marker}', 'w').write(' '.join(sys.argv[1:]))\n",
        )
        command = LintCommand(command=f"{sys.executable} {script}", fix_flag="--fix")

        await apply_lint_fixes(self.working_dir, [command], timeout=5)

        self.assertEqual(marker.read_text(), "--fix")

    async def test_apply_lint_fixes_skips_commands_without_a_fix_flag(self):
        marker = self.scripts_dir / "should-not-exist.txt"
        script = _write_script(
            self.scripts_dir, "record_argv.py", f"open(r'{marker}', 'w').write('ran')\n"
        )
        command = LintCommand(command=f"{sys.executable} {script}")

        await apply_lint_fixes(self.working_dir, [command], timeout=5)

        self.assertFalse(marker.exists())

    async def test_check_lint_commands_reports_failing_commands_only(self):
        passing = LintCommand(
            command=f"{sys.executable} {_write_script(self.scripts_dir, 'ok.py', '')}"
        )
        failing_script = _write_script(
            self.scripts_dir, "fail.py", "print('boom')\nraise SystemExit(1)\n"
        )
        failing = LintCommand(command=f"{sys.executable} {failing_script}")

        problems = await check_lint_commands(
            self.working_dir, [passing, failing], timeout=5
        )

        self.assertEqual(len(problems), 1)
        self.assertIn(f"$ {failing.command}", problems[0])
        self.assertIn("boom", problems[0])

    async def test_check_lint_commands_reports_a_timeout(self):
        script = _write_script(
            self.scripts_dir, "hang.py", "import time\ntime.sleep(30)\n"
        )
        command = LintCommand(command=f"{sys.executable} {script}")

        problems = await check_lint_commands(self.working_dir, [command], timeout=0.2)

        self.assertEqual(len(problems), 1)
        self.assertIn("Timed out", problems[0])


if __name__ == "__main__":
    unittest.main()
