import unittest

from meow.integrations import branch_reviewer


class SanitizeTests(unittest.TestCase):
    def test_replaces_unsafe_characters_with_dashes(self):
        self.assertEqual(branch_reviewer._sanitize("feature/add-x"), "feature-add-x")

    def test_strips_leading_and_trailing_dashes(self):
        self.assertEqual(branch_reviewer._sanitize("/feature/"), "feature")


if __name__ == "__main__":
    unittest.main()
