import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from meow import branch_reviewer


class SanitizeTests(unittest.TestCase):
    def test_replaces_unsafe_characters_with_dashes(self):
        self.assertEqual(branch_reviewer._sanitize("feature/add-x"), "feature-add-x")

    def test_strips_leading_and_trailing_dashes(self):
        self.assertEqual(branch_reviewer._sanitize("/feature/"), "feature")


class RunBranchReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_worktree_mode_creates_worktree_and_reviews_it(self):
        config = {
            "models": {"reviewer": "x", "review_fixer": "x"},
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 2,
            "lint_timeout": 60,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir)
            worktree_dir = working_dir / ".worktrees" / "branch-review-feature-x"

            with (
                patch("meow.branch_reviewer.load_config", return_value=config),
                patch(
                    "meow.branch_reviewer._ensure_existing_branch_worktree",
                    return_value=worktree_dir,
                ) as mock_ensure,
                patch(
                    "meow.branch_reviewer.ReviewerAgent.review_branch",
                    new=AsyncMock(return_value=("PASS", "STATUS: PASS\n")),
                ) as mock_review,
            ):
                result = await branch_reviewer.run_branch_review(
                    working_dir, "feature/x", "main"
                )

            self.assertIsNone(result)
            mock_ensure.assert_called_once_with(
                working_dir, "branch-review-feature-x", "feature/x"
            )
            mock_review.assert_awaited_once_with("main", "feature/x")

    async def test_in_place_mode_requires_the_branch_to_be_checked_out(self):
        config = {
            "models": {"reviewer": "x"},
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 2,
            "lint_timeout": 60,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir)

            with (
                patch("meow.branch_reviewer.load_config", return_value=config),
                patch(
                    "meow.branch_reviewer._require_branch_checked_out",
                    side_effect=RuntimeError("wrong branch"),
                ) as mock_require,
                self.assertRaisesRegex(RuntimeError, "wrong branch"),
            ):
                await branch_reviewer.run_branch_review(
                    working_dir, "feature/x", "main", use_worktree=False
                )

            mock_require.assert_called_once_with(working_dir, "feature/x")

    async def test_passing_on_round_one_never_starts_the_fix_loop(self):
        config = {
            "models": {"reviewer": "x", "review_fixer": "x"},
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 2,
            "lint_timeout": 60,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir)

            with (
                patch("meow.branch_reviewer.load_config", return_value=config),
                patch("meow.branch_reviewer._require_branch_checked_out"),
                patch(
                    "meow.branch_reviewer.ReviewerAgent.review_branch",
                    new=AsyncMock(return_value=("PASS", "STATUS: PASS\n")),
                ),
                patch("meow.orchestrator.ReviewFixAgent") as mock_fixer_cls,
            ):
                result = await branch_reviewer.run_branch_review(
                    working_dir, "feature/x", "main", use_worktree=False
                )

            self.assertIsNone(result)
            mock_fixer_cls.assert_not_called()

    async def test_raises_after_max_rounds_still_failing(self):
        config = {
            "models": {"reviewer": "x", "review_fixer": "x"},
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 1,
            "lint_timeout": 60,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir)

            with (
                patch("meow.branch_reviewer.load_config", return_value=config),
                patch("meow.branch_reviewer._require_branch_checked_out"),
                patch(
                    "meow.branch_reviewer.ReviewerAgent.review_branch",
                    new=AsyncMock(return_value=("FAIL", "STATUS: FAIL\n")),
                ),
                self.assertRaisesRegex(RuntimeError, "did not pass"),
            ):
                await branch_reviewer.run_branch_review(
                    working_dir, "feature/x", "main", use_worktree=False
                )


if __name__ == "__main__":
    unittest.main()
