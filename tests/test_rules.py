import tempfile
import unittest
from pathlib import Path

from meow.rules import _parse_rules_text, load_rules


class ParseRulesTextTests(unittest.TestCase):
    def test_global_only_text_with_no_role_headings(self):
        global_text, role_sections = _parse_rules_text(
            "Always write tests first.\nUse snake_case for filenames.\n"
        )

        self.assertEqual(
            global_text, "Always write tests first.\nUse snake_case for filenames."
        )
        self.assertEqual(role_sections, {})

    def test_role_specific_section_is_isolated_to_that_role(self):
        text = (
            "Global rule.\n\n"
            "## Reviewer\n"
            "Use Chrome DevTools to test edge cases.\n"
        )

        global_text, role_sections = _parse_rules_text(text)

        self.assertEqual(global_text, "Global rule.")
        self.assertEqual(
            role_sections,
            {"reviewer": "Use Chrome DevTools to test edge cases."},
        )

    def test_mixed_global_and_multiple_role_sections(self):
        text = (
            "Global rule applies to everyone.\n\n"
            "## Generator\n"
            "Never edit files under vendor/.\n\n"
            "## Reviewer\n"
            "Use Chrome DevTools to test edge cases.\n"
        )

        global_text, role_sections = _parse_rules_text(text)

        self.assertEqual(global_text, "Global rule applies to everyone.")
        self.assertEqual(
            role_sections,
            {
                "generator": "Never edit files under vendor/.",
                "reviewer": "Use Chrome DevTools to test edge cases.",
            },
        )

    def test_case_insensitive_headings(self):
        text = "## REVIEWER\nUpper-case heading rule.\n"

        _, role_sections = _parse_rules_text(text)

        self.assertEqual(role_sections, {"reviewer": "Upper-case heading rule."})

    def test_duplicate_role_headings_merge(self):
        text = (
            "## Reviewer\n"
            "First reviewer rule.\n\n"
            "## Reviewer\n"
            "Second reviewer rule.\n"
        )

        _, role_sections = _parse_rules_text(text)

        self.assertEqual(
            role_sections["reviewer"],
            "First reviewer rule.\n\nSecond reviewer rule.",
        )

    def test_unrelated_heading_does_not_start_a_role_section(self):
        text = (
            "## Notes\n"
            "This is background, not a role name.\n\n"
            "## Reviewer\n"
            "Use Chrome DevTools to test edge cases.\n"
        )

        global_text, role_sections = _parse_rules_text(text)

        self.assertIn("## Notes", global_text)
        self.assertIn("background", global_text)
        self.assertEqual(
            role_sections, {"reviewer": "Use Chrome DevTools to test edge cases."}
        )

    def test_heading_containing_a_role_word_as_substring_is_not_a_role_heading(self):
        text = "## Reviewer Notes\nThis should stay global text.\n"

        global_text, role_sections = _parse_rules_text(text)

        self.assertIn("Reviewer Notes", global_text)
        self.assertEqual(role_sections, {})

    def test_wrong_heading_level_is_not_a_role_heading(self):
        text = "### Reviewer\nThis should stay global text (h3, not h2).\n"

        global_text, role_sections = _parse_rules_text(text)

        self.assertIn("### Reviewer", global_text)
        self.assertEqual(role_sections, {})


class LoadRulesTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.active_dir = Path(self._tmpdir.name)
        (self.active_dir / "docs").mkdir()

    def _write_rules(self, text: str) -> None:
        (self.active_dir / "docs" / "RULES.md").write_text(text, encoding="utf-8")

    def test_returns_none_when_rules_file_is_missing(self):
        self.assertIsNone(load_rules(self.active_dir, "reviewer"))

    def test_returns_none_when_rules_file_is_empty(self):
        self._write_rules("   \n\n  ")

        self.assertIsNone(load_rules(self.active_dir, "reviewer"))

    def test_labels_the_returned_block_clearly(self):
        self._write_rules("Write tests first.\n")

        result = load_rules(self.active_dir, "reviewer")

        self.assertIsNotNone(result)
        self.assertIn("Project rules", result)
        self.assertIn("must follow", result)
        self.assertIn("Write tests first.", result)

    def test_global_text_applies_to_every_role(self):
        self._write_rules("Write tests first.\n")

        for role in ("explorer", "planner", "generator", "reviewer"):
            with self.subTest(role=role):
                result = load_rules(self.active_dir, role)
                self.assertIsNotNone(result)
                self.assertIn("Write tests first.", result)

    def test_role_specific_text_only_reaches_that_role(self):
        self._write_rules(
            "Global rule.\n\n## Reviewer\nUse Chrome DevTools to test edge cases.\n"
        )

        reviewer_result = load_rules(self.active_dir, "reviewer")
        generator_result = load_rules(self.active_dir, "generator")

        self.assertIn("Global rule.", reviewer_result)
        self.assertIn("Use Chrome DevTools", reviewer_result)
        self.assertIn("Global rule.", generator_result)
        self.assertNotIn("Chrome DevTools", generator_result)

    def test_global_text_precedes_role_text_in_the_combined_result(self):
        self._write_rules("Global rule.\n\n## Reviewer\nRole rule.\n")

        result = load_rules(self.active_dir, "reviewer")

        self.assertLess(result.index("Global rule."), result.index("Role rule."))


if __name__ == "__main__":
    unittest.main()
