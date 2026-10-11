import os
import tempfile
import unittest
from pathlib import Path

from meow.project import plan_files


class DetectReviewFlavorTests(unittest.TestCase):
    def test_plan_based_review_file(self):
        self.assertEqual(
            plan_files._detect_review_flavor(Path("docs/add-csv-export-review.md")),
            "plan",
        )

    def test_prompt_based_review_file(self):
        self.assertEqual(
            plan_files._detect_review_flavor(Path("docs/review.md")), "prompt"
        )

    def test_gitlab_based_review_file(self):
        self.assertEqual(
            plan_files._detect_review_flavor(Path("docs/gitlab-review.md")),
            "gitlab",
        )

    def test_github_based_review_file(self):
        self.assertEqual(
            plan_files._detect_review_flavor(Path("docs/github-review.md")),
            "github",
        )

    def test_branch_based_review_file(self):
        self.assertEqual(
            plan_files._detect_review_flavor(Path("docs/branch-review.md")),
            "branch",
        )

    def test_unrecognized_file_raises_a_clear_error(self):
        with self.assertRaisesRegex(ValueError, "doesn't look like a review file"):
            plan_files._detect_review_flavor(Path("docs/notes.md"))


class LatestReviewFileTests(unittest.TestCase):
    def test_raises_when_nothing_found(self):
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            self.assertRaises(FileNotFoundError),
        ):
            plan_files._latest_review_file(Path(tmpdir))

    def test_picks_the_most_recently_modified_review_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            docs_dir = Path(tmpdir)
            older = docs_dir / "review.md"
            older.write_text("STATUS: FAIL", encoding="utf-8")
            newer = docs_dir / "feature-review.md"
            newer.write_text("STATUS: FAIL", encoding="utf-8")

            os.utime(newer, (older.stat().st_atime + 10,) * 2)

            self.assertEqual(plan_files._latest_review_file(docs_dir), newer)


if __name__ == "__main__":
    unittest.main()


def test_reviewed_plan_file_inverts_plan_review_file():
    plan = Path("docs") / "add-export.md"
    assert plan_files.reviewed_plan_file(plan_files.plan_review_file(plan)) == plan
