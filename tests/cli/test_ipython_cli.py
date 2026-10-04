import unittest

from meow.cli.ipython_cli import _dispatch_line


class DispatchLineTests(unittest.TestCase):
    def test_shell_like_arguments_are_forwarded_to_cli(self):
        received = []

        _dispatch_line('run "Add CSV export" --name csv-export', received.extend)

        self.assertEqual(
            received,
            ["run", "Add CSV export", "--name", "csv-export"],
        )


if __name__ == "__main__":
    unittest.main()
