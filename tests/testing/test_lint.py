import sys
import tempfile
import unittest
from pathlib import Path

from meow.infrastructure.lint import (
    apply_lint_fixes,
    check_lint_commands,
    make_lint_hook,
)
from meow.project.config_models import LintCommand


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
        script = _write_script(self.scripts_dir, "fail.py", "raise SystemExit(1)\n")
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

    async def test_fix_mode_rechecks_and_reports_remaining_findings(self):
        script = _write_script(
            self.scripts_dir,
            "fix_then_check.py",
            "import sys\n"
            "if '--fix' in sys.argv: raise SystemExit(0)\n"
            "print('remaining finding')\nraise SystemExit(1)\n",
        )
        command = LintCommand(command=f"{sys.executable} {script}", fix_flag="--fix")
        hook = make_lint_hook(self.working_dir, [command], timeout=5)
        result = await hook(
            {"tool_name": "Edit", "tool_input": {"file_path": "foo.py"}}, "id", None
        )
        self.assertIn(
            "remaining finding", result["hookSpecificOutput"]["additionalContext"]
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
            f"import sys\nopen(r'{marker}', 'w').write(' '.join(sys.argv[1:]))\n",
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

    async def test_per_file_routes_by_component_filters_and_passes_relative_path(self):
        root = self.scripts_dir / "repo"
        web = root / "apps" / "web"
        api = root / "services" / "api"
        web.mkdir(parents=True)
        api.mkdir(parents=True)
        marker = self.scripts_dir / "web-path.txt"
        script = _write_script(
            self.scripts_dir,
            "capture.py",
            "import os,sys\n"
            f"open(r'{marker}', 'w').write(os.getcwd()+'|'+sys.argv[-1])\n",
        )
        entry = LintCommand(
            command=f"{sys.executable} {script}",
            cwd=Path("apps/web"),
            include=(Path("apps/web/src"),),
            exclude=(Path("apps/web/src/generated"),),
        )
        hook = make_lint_hook(root, [entry], timeout=5)
        await hook(
            {"tool_name": "Write", "tool_input": {"file_path": r"apps\web\src\a.py"}},
            "id",
            None,
        )
        self.assertEqual(
            marker.read_text(), str(web.resolve()) + "|" + str(Path("src/a.py"))
        )
        marker.unlink()
        await hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "apps/website/src/a.py"},
            },
            "id",
            None,
        )
        await hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "apps/web/src/generated/a.py"},
            },
            "id",
            None,
        )
        await hook(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "services/api/src/a.py"},
            },
            "id",
            None,
        )
        self.assertFalse(marker.exists())

    async def test_project_wide_commands_use_component_cwd_env_and_entry_timeout(self):
        root = self.scripts_dir / "repo"
        (root / "apps" / "web").mkdir(parents=True)
        (root / "services" / "api").mkdir(parents=True)
        marker = self.scripts_dir / "runs.txt"
        script = _write_script(
            self.scripts_dir,
            "record.py",
            "import os\n"
            + (
                f"open(r'{marker}', 'a').write("
                "os.getcwd()+'|'+os.getenv('COMPONENT','')+'\\n')\n"
            ),
        )
        commands = [
            LintCommand(
                command=f"{sys.executable} {script}",
                cwd=Path("apps/web"),
                env={"COMPONENT": "web"},
                timeout=2,
            ),
            LintCommand(
                command=f"{sys.executable} {script}",
                cwd=Path("services/api"),
                env={"COMPONENT": "api"},
                timeout=2,
            ),
        ]
        self.assertEqual(await check_lint_commands(root, commands, timeout=20), [])
        self.assertEqual(
            marker.read_text(encoding="utf-8").splitlines(),
            [
                str((root / "apps/web").resolve()) + "|web",
                str((root / "services/api").resolve()) + "|api",
            ],
        )

    async def test_check_only_does_not_append_fix_flag(self):
        marker = self.scripts_dir / "argv.txt"
        script = _write_script(
            self.scripts_dir,
            "record.py",
            "import sys\n" + f"open(r'{marker}', 'w').write(' '.join(sys.argv[1:]))\n",
        )
        command = LintCommand(command=f"{sys.executable} {script}", fix_flag="--fix")
        await check_lint_commands(self.working_dir, [command], timeout=5)
        self.assertEqual(marker.read_text(), "")


if __name__ == "__main__":
    unittest.main()
