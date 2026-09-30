import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from meow.config import (
    LintCommand,
    _os_mismatch,
    _program_name,
    _validate_os_compatibility,
    load_config,
)


class ProgramNameTests(unittest.TestCase):
    def test_takes_the_first_token_lowercased(self):
        self.assertEqual(_program_name("Ruff check --fix"), "ruff")

    def test_strips_a_windows_path_prefix(self):
        self.assertEqual(
            _program_name(r"C:\tools\Scripts\lint.BAT --all"), "lint.bat"
        )

    def test_strips_a_posix_path_prefix(self):
        self.assertEqual(_program_name("/usr/local/bin/eslint ."), "eslint")


class WindowsOnlyMarkerTests(unittest.TestCase):
    def test_batch_script_flagged_off_windows(self):
        problem = _os_mismatch("lint.bat --check", "Linux")
        self.assertIsNotNone(problem)
        self.assertIn("Windows-only", problem)

    def test_cmd_exe_flagged_off_windows(self):
        problem = _os_mismatch("cmd.exe /c lint.bat", "Darwin")
        self.assertIsNotNone(problem)

    def test_windows_powershell_flagged_off_windows(self):
        problem = _os_mismatch("powershell.exe -File lint.ps1", "Linux")
        self.assertIsNotNone(problem)

    def test_batch_script_is_fine_on_windows(self):
        self.assertIsNone(_os_mismatch("lint.bat --check", "Windows"))
        self.assertIsNone(_os_mismatch("powershell.exe -File lint.ps1", "Windows"))


class UnixOnlyMarkerTests(unittest.TestCase):
    def test_shell_script_flagged_on_windows(self):
        problem = _os_mismatch("lint.sh --check", "Windows")
        self.assertIsNotNone(problem)
        self.assertIn("Unix shell", problem)

    def test_bash_flagged_on_windows(self):
        problem = _os_mismatch("bash scripts/lint.sh", "Windows")
        self.assertIsNotNone(problem)

    def test_wsl_launcher_is_never_flagged_on_windows(self):
        self.assertIsNone(_os_mismatch("wsl bash scripts/lint.sh", "Windows"))
        self.assertIsNone(_os_mismatch("wsl.exe ./lint.sh", "Windows"))

    def test_shell_script_is_fine_on_linux_and_macos(self):
        self.assertIsNone(_os_mismatch("lint.sh --check", "Linux"))
        self.assertIsNone(_os_mismatch("bash scripts/lint.sh", "Darwin"))


class NoFalsePositiveTests(unittest.TestCase):
    def test_pwsh_core_is_never_flagged_anywhere(self):
        # pwsh (PowerShell 7+/Core) is genuinely cross-platform, unlike
        # powershell.exe (Windows PowerShell 5.1) -- it says nothing about
        # which OS the command expects.
        self.assertIsNone(_os_mismatch("pwsh -File lint.ps1", "Linux"))
        self.assertIsNone(_os_mismatch("pwsh -File lint.ps1", "Windows"))

    def test_cross_platform_commands_are_never_flagged(self):
        for command, system in [
            ("ruff check", "Windows"),
            ("npx oxlint", "Windows"),
            ("golangci-lint run", "Windows"),
            ("eslint .", "Linux"),
            ("npx eslint .", "Darwin"),
        ]:
            with self.subTest(command=command, system=system):
                self.assertIsNone(_os_mismatch(command, system))

    def test_not_on_path_is_not_treated_as_an_os_mismatch(self):
        # A bare program name that simply isn't installed yet carries none
        # of the explicit markers this check looks for -- that is a normal,
        # expected failure the lint run itself reports, not this check's job.
        self.assertIsNone(_os_mismatch("some-tool-nobody-has-installed", "Windows"))
        self.assertIsNone(_os_mismatch("some-tool-nobody-has-installed", "Linux"))


class ValidateOsCompatibilityTests(unittest.TestCase):
    def test_raises_a_clear_error_naming_the_offending_command(self):
        commands = [LintCommand(command="ruff check"), LintCommand(command="lint.sh")]
        with self.assertRaisesRegex(ValueError, "lint.sh"):
            _validate_os_compatibility(commands, system="Windows")

    @staticmethod
    def test_passes_silently_when_nothing_is_mismatched():
        commands = [LintCommand(command="ruff check"), LintCommand(command="npx tsc")]
        _validate_os_compatibility(commands, system="Windows")  # no raise
        _validate_os_compatibility(commands, system="Linux")  # no raise

    @staticmethod
    def test_defaults_to_the_real_host_os_when_not_given():
        commands = [LintCommand(command="ruff check")]
        _validate_os_compatibility(commands)  # no raise, whatever the host OS


class LoadConfigOsValidationTests(unittest.TestCase):
    def test_load_config_rejects_a_mismatched_lint_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            (working_dir / ".harness.toml").write_text(
                '[[lint]]\ncommand = "lint.bat --check"\n', encoding="utf-8"
            )
            with (
                patch("meow.config.platform.system", return_value="Linux"),
                self.assertRaisesRegex(ValueError, "lint.bat"),
            ):
                load_config(working_dir)

    def test_load_config_accepts_a_cross_platform_lint_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            (working_dir / ".harness.toml").write_text(
                '[[lint]]\ncommand = "ruff check"\n', encoding="utf-8"
            )
            config = load_config(working_dir)  # no raise, on whatever host OS
            self.assertEqual(config["lint"][0].command, "ruff check")


if __name__ == "__main__":
    unittest.main()
