import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from meow.project.config import (
    LintCommand,
    _lint_entry,
    _normalize_lint_commands,
    _normalize_tester_config,
    _os_mismatch,
    _program_name,
    _validate_max_rounds,
    _validate_os_compatibility,
    load_config,
    resolve_command_cwd,
)


class ProgramNameTests(unittest.TestCase):
    def test_takes_the_first_token_lowercased(self):
        self.assertEqual(_program_name("Ruff check --fix"), "ruff")

    def test_strips_a_windows_path_prefix(self):
        self.assertEqual(_program_name(r"C:\tools\Scripts\lint.BAT --all"), "lint.bat")

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
    def test_bare_shell_script_always_flagged_on_windows(self):
        # Windows has no shebang support -- a bare .sh filename can't be
        # exec'd directly even with a real bash on PATH, unlike `bash
        # script.sh`, which explicitly names its interpreter.
        with patch("meow.project.config.shutil.which", return_value=r"C:\Git\bin\bash.exe"):
            problem = _os_mismatch("lint.sh --check", "Windows")
        self.assertIsNotNone(problem)
        self.assertIn("Unix shell", problem)

    def test_bash_flagged_on_windows_when_no_interpreter_resolves(self):
        with patch("meow.project.config.shutil.which", return_value=None):
            problem = _os_mismatch("bash scripts/lint.sh", "Windows")
        self.assertIsNotNone(problem)

    def test_wsl_launcher_is_never_flagged_on_windows(self):
        self.assertIsNone(_os_mismatch("wsl bash scripts/lint.sh", "Windows"))
        self.assertIsNone(_os_mismatch("wsl.exe ./lint.sh", "Windows"))

    def test_shell_script_is_fine_on_linux_and_macos(self):
        self.assertIsNone(_os_mismatch("lint.sh --check", "Linux"))
        self.assertIsNone(_os_mismatch("bash scripts/lint.sh", "Darwin"))


class GitBashExemptionTests(unittest.TestCase):
    """bash/sh/zsh invoked explicitly (not a bare .sh filename) are only
    flagged on Windows if no such interpreter actually resolves on PATH --
    Git for Windows (an extremely common install) puts a real bash.exe
    there, and a command that explicitly invokes it genuinely works."""

    def test_bash_on_path_is_not_flagged_on_windows(self):
        with patch("meow.project.config.shutil.which", return_value=r"C:\Git\bin\bash.exe"):
            self.assertIsNone(_os_mismatch("bash scripts/lint.sh", "Windows"))

    def test_sh_on_path_is_not_flagged_on_windows(self):
        with patch("meow.project.config.shutil.which", return_value=r"C:\Git\bin\sh.exe"):
            self.assertIsNone(_os_mismatch("sh scripts/lint.sh", "Windows"))

    def test_zsh_on_path_is_not_flagged_on_windows(self):
        with patch("meow.project.config.shutil.which", return_value="/usr/bin/zsh"):
            self.assertIsNone(_os_mismatch("zsh scripts/lint.sh", "Windows"))

    def test_bash_not_on_path_is_still_flagged_on_windows(self):
        with patch("meow.project.config.shutil.which", return_value=None):
            problem = _os_mismatch("bash scripts/lint.sh", "Windows")
        self.assertIsNotNone(problem)
        self.assertIn("Unix shell", problem)

    def test_bare_sh_extension_is_flagged_even_with_bash_on_path(self):
        # `lint.sh` alone (no explicit `bash`/`sh` in front) still can't be
        # exec'd directly by Windows, no matter what's on PATH.
        with patch("meow.project.config.shutil.which", return_value=r"C:\Git\bin\bash.exe"):
            problem = _os_mismatch("lint.sh --check", "Windows")
        self.assertIsNotNone(problem)

    def test_path_is_only_consulted_on_windows(self):
        # The PATH check is specifically about whether Windows can run a
        # bash/sh/zsh command at all; it has nothing to say on a platform
        # where these are never flagged in the first place.
        with patch("meow.project.config.shutil.which", return_value=None) as which:
            self.assertIsNone(_os_mismatch("bash scripts/lint.sh", "Linux"))
        which.assert_not_called()


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
                patch("meow.project.config.platform.system", return_value="Linux"),
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


class ValidateMaxRoundsTests(unittest.TestCase):
    """max_rounds <= 0 doesn't crash in any round-loop shape -- every one
    computes an empty range() and falls straight through to "didn't pass"
    -- but that means a real planner call (and sometimes a wasted
    generator/fixer session start) runs first, only to report a confusing
    "did not pass after 0 rounds" with no round ever actually attempted.
    Confirmed via a real `meow run` with max_rounds=0: the planner ran for
    real, then the sprint failed immediately with the generator never
    invoked. These catch it at config load instead."""

    def test_rejects_zero(self):
        with self.assertRaisesRegex(ValueError, "max_rounds"):
            _validate_max_rounds({"max_rounds": 0})

    def test_rejects_negative(self):
        with self.assertRaisesRegex(ValueError, "max_rounds"):
            _validate_max_rounds({"max_rounds": -1})

    def test_rejects_a_boolean(self):
        # bool is a subclass of int in Python -- True would otherwise pass
        # an isinstance(..., int) check and even satisfy >= 1.
        with self.assertRaisesRegex(ValueError, "max_rounds"):
            _validate_max_rounds({"max_rounds": True})

    def test_rejects_a_non_integer(self):
        with self.assertRaisesRegex(ValueError, "max_rounds"):
            _validate_max_rounds({"max_rounds": "8"})

    @staticmethod
    def test_accepts_one():
        _validate_max_rounds({"max_rounds": 1})  # no raise

    @staticmethod
    def test_accepts_the_default():
        _validate_max_rounds({"max_rounds": 8})  # no raise

    def test_load_config_rejects_max_rounds_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            (working_dir / ".harness.toml").write_text(
                'max_rounds = 0\n[[lint]]\ncommand = "ruff check"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "max_rounds"):
                load_config(working_dir)


class LintEntryTests(unittest.TestCase):  # ruff: ignore[too-many-public-methods]
    """`_lint_entry` validates one [[lint]] table. No test here exercised
    this at all before -- found while adversarially checking zero/one/many
    lint command counts; confirmed the zero-commands case for real via
    `meow plan` first (a clean, pre-agent ValueError), then backfilled
    direct coverage for the whole surface."""

    def test_missing_command_key_raises(self):
        with self.assertRaisesRegex(ValueError, "must set 'command'"):
            _lint_entry({"fix_flag": "--fix"}, 1)

    def test_empty_command_string_raises(self):
        with self.assertRaisesRegex(ValueError, "must set 'command'"):
            _lint_entry({"command": ""}, 1)

    def test_non_dict_entry_raises(self):
        with self.assertRaisesRegex(ValueError, "must set 'command'"):
            _lint_entry("ruff check", 1)

    def test_unknown_key_raises_naming_entry_and_key(self):
        with self.assertRaisesRegex(ValueError, r"entry 2 has unknown key\(s\)"):
            _lint_entry({"command": "ruff check", "typo_key": True}, 2)

    def test_valid_entry_applies_defaults(self):
        entry = _lint_entry({"command": "ruff check"}, 1)
        self.assertEqual(entry.command, "ruff check")
        self.assertIsNone(entry.fix_flag)
        self.assertTrue(entry.per_file)
        self.assertTrue(entry.gate)

    def test_valid_entry_honors_all_fields(self):
        entry = _lint_entry(
            {
                "command": "mypy .",
                "fix_flag": None,
                "per_file": False,
                "gate": False,
            },
            1,
        )
        self.assertFalse(entry.per_file)
        self.assertFalse(entry.gate)

    def test_normalizes_extended_lint_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "apps" / "web").mkdir(parents=True)
            entry = _lint_entry(
                {
                    "command": "ruff check",
                    "cwd": "apps\\web",
                    "include": ["src", "tests"],
                    "exclude": ["tests/slow"],
                    "timeout": 12.5,
                    "env": {"MODE": "fast"},
                    "args": ["--output-file", "a report.json"],
                },
                1,
            )
            self.assertEqual(entry.cwd, Path("apps/web"))
            self.assertEqual(entry.include, (Path("src"), Path("tests")))
            self.assertEqual(entry.exclude, (Path("tests/slow"),))
            self.assertEqual(entry.timeout, 12.5)
            self.assertEqual(entry.env, {"MODE": "fast"})
            self.assertEqual(entry.args, ("--output-file", "a report.json"))
            self.assertEqual(
                entry.argv(), ["ruff", "check", "--output-file", "a report.json"]
            )
            self.assertEqual(
                resolve_command_cwd(root, entry.cwd), (root / "apps/web").resolve()
            )

    def test_rejects_bad_lint_types_and_timeouts(self):
        invalid = [
            {"per_file": "false"},
            {"gate": 1},
            {"cwd": 3},
            {"include": "src"},
            {"exclude": ["src", 2]},
            {"timeout": True},
            {"timeout": 0},
            {"timeout": -1},
            {"env": {"X": 2}},
            {"args": ["ok", 2]},
        ]
        for fields in invalid:
            with (
                self.subTest(fields=fields),
                self.assertRaisesRegex(ValueError, r"\[\[lint\]\] entry 3"),
            ):
                _lint_entry({"command": "ruff check", **fields}, 3)

    def test_rejects_absolute_or_escaping_lint_paths(self):
        for field, value in [
            ("cwd", "../outside"),
            ("cwd", "C:/outside"),
            ("include", ["../outside"]),
            ("exclude", ["/outside"]),
        ]:
            with (
                self.subTest(field=field, value=value),
                self.assertRaisesRegex(ValueError, r"\[\[lint\]\] entry 1"),
            ):
                _lint_entry({"command": "ruff check", field: value}, 1)

    def test_component_prefix_matching_does_not_match_sibling_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "apps" / "web").mkdir(parents=True)
            (root / "apps" / "website").mkdir(parents=True)
            entry = _lint_entry({"command": "ruff check", "cwd": "apps/web"}, 1)
            self.assertTrue(entry.matches_file(Path("apps/web/src/a.py")))
            self.assertFalse(entry.matches_file(Path("apps/website/src/a.py")))

    def test_component_include_and_exclude_use_repo_relative_paths(self):
        entry = _lint_entry(
            {
                "command": "ruff check",
                "cwd": "apps/web",
                "include": ["apps/web/src"],
                "exclude": ["apps/web/src/generated"],
            },
            1,
        )
        self.assertTrue(entry.matches_file(Path("apps/web/src/app.py")))
        self.assertFalse(entry.matches_file(Path("apps/web/src/generated/code.py")))
        self.assertFalse(entry.matches_file(Path("apps/website/src/app.py")))


class TesterConfigTests(unittest.TestCase):
    def test_empty_tester_config_is_valid(self):
        tester = _normalize_tester_config({})
        self.assertEqual(tester["tests"], [])
        self.assertEqual(tester["dev_server"], [])
        self.assertEqual(tester["mcp"], [])

    def test_normalizes_tester_commands_and_quoted_args(self):
        tester = _normalize_tester_config({
            "tester": {
                "base_url": "http://localhost:3000",
                "test_dirs": ["apps/web/tests"],
                "architecture_files": ["docs/ARCHITECTURE.md"],
                "tests": [
                    {
                        "cwd": "apps/web",
                        "command": "python -m pytest",
                        "args": ["--junitxml", "test results.xml"],
                        "timeout": 45,
                        "env": {"CI": "1"},
                        "gate": False,
                    }
                ],
                "dev_server": [
                    {
                        "cwd": "apps/web",
                        "command": "npm run dev",
                        "args": ["--", "--host"],
                        "ready_url": "http://localhost:3000/health",
                        "startup_timeout": 10,
                    }
                ],
                "mcp": [{"name": "browser", "command": "npx", "args": ["browser-mcp"]}],
            }
        })
        self.assertEqual(tester["tests"][0].args, ("--junitxml", "test results.xml"))
        self.assertFalse(tester["tests"][0].gate)
        self.assertEqual(tester["dev_server"][0].startup_timeout, 10)
        self.assertEqual(tester["mcp"][0]["args"], ["browser-mcp"])

    def test_rejects_bad_tester_entries_and_duplicate_mcp_names(self):
        cases = [
            {"tests": [{"command": "pytest", "timeout": True}]},
            {"tests": [{"command": "pytest", "timeout": 0}]},
            {"tests": [{"command": "pytest", "cwd": "../outside"}]},
            {"dev_server": [{"command": "npm start"}]},
            {
                "dev_server": [
                    {
                        "command": "npm start",
                        "ready_url": "http://localhost",
                        "startup_timeout": False,
                    }
                ]
            },
            {
                "mcp": [
                    {"name": "same", "command": "one"},
                    {"name": "same", "command": "two"},
                ]
            },
        ]
        for fields in cases:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                _normalize_tester_config({"tester": fields})

    def test_resolve_command_cwd_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            try:
                (root / "linked").symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation unavailable")
            with self.assertRaisesRegex(ValueError, "inside"):
                resolve_command_cwd(root, Path("linked"))

    def test_load_config_ignores_fully_commented_tester_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, ".harness.toml").write_text(
                (
                    '[[lint]]\ncommand = "ruff check"\n\n# [tester]\n'
                    '# base_url = "http://localhost"\n# [[tester.tests]]\n'
                    '# command = "pytest"\n'
                ),
                encoding="utf-8",
            )
            self.assertEqual(load_config(Path(tmp))["tester"]["tests"], [])


class NormalizeLintCommandsTests(unittest.TestCase):
    def test_zero_commands_raises(self):
        with self.assertRaisesRegex(ValueError, "must define at least one"):
            _normalize_lint_commands({})

    def test_one_command_via_the_list_form(self):
        commands = _normalize_lint_commands({"lint": [{"command": "ruff check"}]})
        self.assertEqual([c.command for c in commands], ["ruff check"])

    def test_many_commands_preserve_configured_order(self):
        commands = _normalize_lint_commands({
            "lint": [
                {"command": "ruff check"},
                {"command": "mypy .", "gate": False},
                {"command": "npx eslint ."},
            ]
        })
        self.assertEqual(
            [c.command for c in commands], ["ruff check", "mypy .", "npx eslint ."]
        )

    def test_legacy_single_command_form_becomes_one_entry(self):
        commands = _normalize_lint_commands({
            "lint_command": "ruff check",
            "lint_fix_flag": "--fix",
        })
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].command, "ruff check")
        self.assertEqual(commands[0].fix_flag, "--fix")

    def test_legacy_form_and_list_form_combine_legacy_first(self):
        commands = _normalize_lint_commands({
            "lint_command": "ruff check",
            "lint": [{"command": "mypy ."}],
        })
        self.assertEqual([c.command for c in commands], ["ruff check", "mypy ."])

    def test_lint_not_a_list_raises(self):
        with self.assertRaisesRegex(ValueError, "must be a list"):
            _normalize_lint_commands({"lint": {"command": "ruff check"}})

    def test_whitespace_only_command_is_accepted_at_load_but_fails_at_argv(self):
        # _lint_entry's falsy check doesn't catch a whitespace-only string --
        # it's truthy. This isn't caught until the command actually tries to
        # run (LintCommand.argv), not at config-load time like every other
        # lint validation here. Documented as current (deferred) behavior,
        # not fixed: a real typo this obscure is vanishingly unlikely, and
        # every other config mistake here fails at load time specifically
        # so it's worth flagging if that ever changes.
        commands = _normalize_lint_commands({"lint": [{"command": "   "}]})
        with self.assertRaisesRegex(ValueError, "lint command is empty"):
            commands[0].argv()


if __name__ == "__main__":
    unittest.main()
