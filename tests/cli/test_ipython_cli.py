import unittest
from unittest.mock import patch

from meow.cli import cli
from meow.cli.ipython_cli import _dispatch_line


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


if __name__ == "__main__":
    unittest.main()
