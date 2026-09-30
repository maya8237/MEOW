import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from meow import orchestrator, review_fix_review


class DetectReviewFlavorTests(unittest.TestCase):
    def test_plan_based_review_file(self):
        self.assertEqual(
            orchestrator._detect_review_flavor(Path("docs/add-csv-export-review.md")),
            "plan",
        )

    def test_prompt_based_review_file(self):
        self.assertEqual(
            orchestrator._detect_review_flavor(Path("docs/review.md")), "prompt"
        )

    def test_gitlab_based_review_file(self):
        self.assertEqual(
            orchestrator._detect_review_flavor(Path("docs/gitlab-review.md")),
            "gitlab",
        )

    def test_branch_based_review_file(self):
        self.assertEqual(
            orchestrator._detect_review_flavor(Path("docs/branch-review.md")),
            "branch",
        )

    def test_unrecognized_file_raises_a_clear_error(self):
        with self.assertRaisesRegex(ValueError, "doesn't look like a review file"):
            orchestrator._detect_review_flavor(Path("docs/notes.md"))


class LatestReviewFileTests(unittest.TestCase):
    def test_raises_when_nothing_found(self):
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            self.assertRaises(FileNotFoundError),
        ):
            orchestrator._latest_review_file(Path(tmpdir))

    def test_picks_the_most_recently_modified_review_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            docs_dir = Path(tmpdir)
            older = docs_dir / "review.md"
            older.write_text("STATUS: FAIL", encoding="utf-8")
            newer = docs_dir / "feature-review.md"
            newer.write_text("STATUS: FAIL", encoding="utf-8")

            os.utime(newer, (older.stat().st_atime + 10,) * 2)

            self.assertEqual(orchestrator._latest_review_file(docs_dir), newer)


class RunReviewFixReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_based_uses_the_existing_verdict_as_round_one(self):
        config = {
            "models": {
                "planner": "x", "generator": "x", "reviewer": "x",
                "explorer": "x", "review_fixer": "x",
            },
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 2,
            "lint_timeout": 60,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir)
            docs_dir = working_dir / "docs"
            docs_dir.mkdir()
            plan_file = docs_dir / "feature.md"
            plan_file.write_text("# Plan", encoding="utf-8")
            review_file = docs_dir / "feature-review.md"
            review_file.write_text(
                "SUMMARY: needs work\nSTATUS: FAIL\ncriterion: FAIL",
                encoding="utf-8",
            )

            mock_generator = MagicMock()
            mock_generator.__aenter__ = AsyncMock(return_value=mock_generator)
            mock_generator.__aexit__ = AsyncMock(return_value=False)
            mock_generator.implement = AsyncMock(return_value="")

            with (
                patch("meow.review_fix_review.load_config", return_value=config),
                patch(
                    "meow.orchestrator.Generator", return_value=mock_generator
                ),
                patch(
                    "meow.orchestrator.ReviewerAgent.review_plan",
                    new_callable=AsyncMock,
                ) as mock_review_plan,
            ):
                mock_review_plan.return_value = ("PASS", "STATUS: PASS\n")

                result = await review_fix_review.run_review_fix_review(
                    working_dir, "Check error handling", review_file
                )

            self.assertIsNone(result)
            mock_generator.implement.assert_awaited_once()
            mock_review_plan.assert_awaited_once_with(
                plan_file, focus="Check error handling"
            )

    async def test_plan_based_raises_when_the_plan_file_is_gone(self):
        config = {
            "models": {"reviewer": "x"},
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 2,
            "lint_timeout": 60,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir)
            docs_dir = working_dir / "docs"
            docs_dir.mkdir()
            review_file = docs_dir / "feature-review.md"
            review_file.write_text("STATUS: FAIL", encoding="utf-8")

            with (
                patch("meow.review_fix_review.load_config", return_value=config),
                self.assertRaisesRegex(FileNotFoundError, "feature.md"),
            ):
                await review_fix_review.run_review_fix_review(
                    working_dir, "Check it", review_file
                )

    async def test_prompt_based_uses_review_fix_agent(self):
        config = {
            "models": {"reviewer": "x", "review_fixer": "x"},
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 2,
            "lint_timeout": 60,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir)
            docs_dir = working_dir / "docs"
            docs_dir.mkdir()
            review_file = docs_dir / "review.md"
            review_file.write_text(
                "SUMMARY: needs work\nSTATUS: FAIL\nrequirement: FAIL",
                encoding="utf-8",
            )

            mock_fixer = AsyncMock()
            mock_fixer.__aenter__ = AsyncMock(return_value=mock_fixer)
            mock_fixer.__aexit__ = AsyncMock(return_value=False)
            mock_fixer.fix = AsyncMock(return_value="")

            with (
                patch("meow.review_fix_review.load_config", return_value=config),
                patch(
                    "meow.orchestrator.ReviewFixAgent", return_value=mock_fixer
                ),
                patch(
                    "meow.orchestrator.ReviewerAgent.review_prompt",
                    new_callable=AsyncMock,
                ) as mock_review_prompt,
            ):
                mock_review_prompt.return_value = ("PASS", "STATUS: PASS\n")

                result = await review_fix_review.run_review_fix_review(
                    working_dir, "Check error handling", review_file
                )

            self.assertIsNone(result)
            mock_fixer.fix.assert_awaited_once()
            mock_review_prompt.assert_awaited_once_with("Check error handling")

    async def test_gitlab_based_raises_a_clear_error_before_any_agent_runs(self):
        config = {
            "models": {},
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 2,
            "lint_timeout": 60,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir)
            docs_dir = working_dir / "docs"
            docs_dir.mkdir()
            review_file = docs_dir / "gitlab-review.md"
            review_file.write_text("STATUS: FAIL", encoding="utf-8")

            with (
                patch("meow.review_fix_review.load_config", return_value=config),
                patch("meow.orchestrator.ReviewFixAgent") as mock_fixer_cls,
                patch("meow.orchestrator.Generator") as mock_generator_cls,
                self.assertRaisesRegex(RuntimeError, "no local checkout"),
            ):
                await review_fix_review.run_review_fix_review(
                    working_dir, "Check it", review_file
                )

            mock_fixer_cls.assert_not_called()
            mock_generator_cls.assert_not_called()

    async def test_branch_based_raises_a_clear_error_before_any_agent_runs(self):
        config = {
            "models": {},
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 2,
            "lint_timeout": 60,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir)
            docs_dir = working_dir / "docs"
            docs_dir.mkdir()
            review_file = docs_dir / "branch-review.md"
            review_file.write_text("STATUS: FAIL", encoding="utf-8")

            with (
                patch("meow.review_fix_review.load_config", return_value=config),
                patch("meow.orchestrator.ReviewFixAgent") as mock_fixer_cls,
                patch("meow.orchestrator.Generator") as mock_generator_cls,
                self.assertRaisesRegex(RuntimeError, "meow branch-review"),
            ):
                await review_fix_review.run_review_fix_review(
                    working_dir, "Check it", review_file
                )

            mock_fixer_cls.assert_not_called()
            mock_generator_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
