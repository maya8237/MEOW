import unittest
from unittest.mock import patch

from meow.cli import cli
from meow.cli.ipython_cli import _dispatch_line
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

    def test_no_suggestions_for_free_text_option_value(self):
        self.assertEqual(complete_words(self.parser, ["run", "--name"], ""), [])


if __name__ == "__main__":
    unittest.main()
