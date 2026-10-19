import unittest
from unittest.mock import patch

from meow.cli import cli
from meow.cli.ipython_cli import _build_magics, _dispatch_line, _magic_names
from meow.cli.ipython_completion import complete_words


class DispatchLineTests(unittest.TestCase):
    def test_shell_like_arguments_are_forwarded_to_cli(self):
        received = []

        _dispatch_line('run "Add CSV export" --name csv-export', received.extend)

        self.assertEqual(
            received,
            ["run", "Add CSV export", "--name", "csv-export"],
        )

    def test_public_parser_exposes_ipython_endpoint(self):
        args = cli._build_arg_parser().parse_args(["ipython"])

        self.assertEqual(args.command, "ipython")

    def test_magic_names_include_shortcuts_and_fun_aliases(self):
        names = _magic_names()

        for name in ("r", "p", "rv", "l", "pounce"):
            self.assertIn(name, names)

    def test_magic_names_exclude_removed_orchestrate(self):
        names = _magic_names()

        self.assertNotIn("orchestrate", names)
        self.assertNotIn("o", names)

    def test_completes_orchestrate_flags(self):
        parser = cli._build_arg_parser()

        self.assertIn("--max-parallel", complete_words(parser, ["run"], "--max"))
        self.assertIn("--dry-run", complete_words(parser, ["r"], "--dry"))

    def test_magic_names_leave_out_ipython_aliases(self):
        names = _magic_names()

        self.assertNotIn("i", names)
        self.assertNotIn("lab", names)
        self.assertNotIn("ipython", names)

    def test_built_magics_expose_shortcuts_as_bare_line_magics(self):
        try:
            magics = _build_magics()
        except ImportError:
            self.skipTest("IPython is not installed")

        line = magics.magics["line"]
        for name in ("r", "rv", "l", "pounce"):
            self.assertIn(name, line)
        self.assertNotIn("lab", line)

    @staticmethod
    def test_public_endpoint_starts_the_existing_ipython_session():
        with patch("meow.cli.cli.start_ipython") as start:
            cli.cli_main(["ipython"])

        start.assert_called_once_with()


class CompleteWordsTests(unittest.TestCase):
    def setUp(self):
        self.parser = cli._build_arg_parser()

    def test_completes_subcommands(self):
        self.assertIn("plan", complete_words(self.parser, [], "pl"))
        self.assertNotIn("run", complete_words(self.parser, [], "pl"))

    def test_completes_flags_for_subcommand(self):
        self.assertIn("--name", complete_words(self.parser, ["run"], "--na"))

    def test_completes_nested_subcommand(self):
        self.assertIn("verify", complete_words(self.parser, ["native"], "ve"))

    def test_completes_flags_for_shortcut(self):
        self.assertIn("--name", complete_words(self.parser, ["r"], "--na"))
        self.assertIn("--lint-fix", complete_words(self.parser, ["l"], "--"))

    def test_no_suggestions_for_free_text_option_value(self):
        self.assertEqual(complete_words(self.parser, ["run", "--name"], ""), [])


if __name__ == "__main__":
    unittest.main()
