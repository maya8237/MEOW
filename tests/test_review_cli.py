import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from meow import review_cli
from meow.review_cli import _validate_review_flags


def _config(**overrides):
    base = {
        "models": {
            "reviewer": "x", "review_fixer": "x", "explorer": "x", "generator": "x",
        },
        "lint": [],
        "docs_dir": "docs",
        "max_rounds": 2,
        "lint_timeout": 60,
    }
    base.update(overrides)
    return base


class ValidateReviewFlagsTests(unittest.TestCase):
    @staticmethod
    def test_nothing_given_is_fine():
        _validate_review_flags(None, None, None, None, None, None, None)  # no raise

    @staticmethod
    def test_prompt_alone_is_fine():
        _validate_review_flags("do it", None, None, None, None, None, None)

    @staticmethod
    def test_prompt_with_review_file_is_fine():
        _validate_review_flags("focus", None, None, None, None, None, Path("r.md"))

    def test_branch_without_target_raises(self):
        with self.assertRaisesRegex(ValueError, "together"):
            _validate_review_flags(None, None, None, "b", None, None, None)

    def test_target_without_branch_raises(self):
        with self.assertRaisesRegex(ValueError, "together"):
            _validate_review_flags(None, None, None, None, "t", None, None)

    def test_two_sources_raises(self):
        with self.assertRaisesRegex(ValueError, "at most one"):
            _validate_review_flags(None, None, "link", "b", "t", None, None)

    def test_review_file_with_another_source_raises(self):
        with self.assertRaisesRegex(ValueError, "--review-file"):
            _validate_review_flags(None, "KEY", None, None, None, None, Path("r.md"))

    def test_prompt_with_another_source_raises(self):
        with self.assertRaisesRegex(ValueError, "not both"):
            _validate_review_flags("text", None, "link", None, None, None, None)


class GitlabFixRejectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_gitlab_and_fix_together_raises_before_fetching(self):
        with (
            patch("meow.review_cli.load_config", return_value=_config()),
            patch(
                "meow.review_cli._gitlab_review", new=AsyncMock()
            ) as mock_gitlab,
            self.assertRaisesRegex(ValueError, "--gitlab.*--fix"),
        ):
            await review_cli.run_review_command(
                Path("/project"), None, fix=True, gitlab_link="https://x/mr/1"
            )
        mock_gitlab.assert_not_awaited()


class PromptSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_only_never_raises_on_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            with (
                patch("meow.review_cli.load_config", return_value=_config()),
                patch(
                    "meow.review_cli.ReviewerAgent.review_prompt",
                    new=AsyncMock(return_value=("FAIL", "STATUS: FAIL\n")),
                ) as mock_review,
            ):
                result = await review_cli.run_review_command(
                    working_dir, "check it", fix=False
                )
            self.assertIsNone(result)
            mock_review.assert_awaited_once_with("check it")

    async def test_fix_loops_and_raises_on_exhaustion(self):
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            with (
                patch("meow.review_cli.load_config", return_value=_config()),
                patch(
                    "meow.review_cli.ReviewerAgent.review_prompt",
                    new=AsyncMock(return_value=("FAIL", "STATUS: FAIL\n")),
                ),
                patch("meow.orchestrator.ReviewFixAgent") as mock_fixer_cls,
                self.assertRaisesRegex(RuntimeError, "did not pass"),
            ):
                await review_cli.run_review_command(working_dir, "check it", fix=True)
            mock_fixer_cls.assert_called_once()

    async def test_fix_passing_on_first_review_never_starts_fix_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            with (
                patch("meow.review_cli.load_config", return_value=_config()),
                patch(
                    "meow.review_cli.ReviewerAgent.review_prompt",
                    new=AsyncMock(return_value=("PASS", "STATUS: PASS\n")),
                ),
                patch("meow.orchestrator.ReviewFixAgent") as mock_fixer_cls,
            ):
                result = await review_cli.run_review_command(
                    working_dir, "check it", fix=True
                )
            self.assertIsNone(result)
            mock_fixer_cls.assert_not_called()


class PlanSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_plan_file_report_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            plan_file = working_dir / "feature.md"
            with (
                patch("meow.review_cli.load_config", return_value=_config()),
                patch(
                    "meow.review_cli.ReviewerAgent.review_plan",
                    new=AsyncMock(return_value=("FAIL", "STATUS: FAIL\n")),
                ) as mock_review,
                patch("meow.orchestrator.Generator") as mock_generator_cls,
            ):
                result = await review_cli.run_review_command(
                    working_dir, None, fix=False, plan_file=plan_file
                )
            self.assertIsNone(result)
            mock_review.assert_awaited_once_with(plan_file)
            mock_generator_cls.assert_not_called()

    async def test_explicit_plan_file_fix_raises_on_exhaustion(self):
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            plan_file = working_dir / "feature.md"
            with (
                patch("meow.review_cli.load_config", return_value=_config()),
                patch(
                    "meow.review_cli.ReviewerAgent.review_plan",
                    new=AsyncMock(return_value=("FAIL", "STATUS: FAIL\n")),
                ),
                patch("meow.orchestrator.Generator") as mock_generator_cls,
                self.assertRaisesRegex(RuntimeError, "did not pass"),
            ):
                await review_cli.run_review_command(
                    working_dir, None, fix=True, plan_file=plan_file
                )
            mock_generator_cls.assert_called_once()

    @staticmethod
    async def test_nothing_given_auto_discovers_latest_plan():
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            docs_dir = working_dir / "docs"
            docs_dir.mkdir()
            plan_file = docs_dir / "feature.md"
            plan_file.write_text("# plan\n", encoding="utf-8")
            with (
                patch("meow.review_cli.load_config", return_value=_config()),
                patch(
                    "meow.review_cli.ReviewerAgent.review_plan",
                    new=AsyncMock(return_value=("PASS", "STATUS: PASS\n")),
                ) as mock_review,
            ):
                await review_cli.run_review_command(working_dir, None, fix=False)
            mock_review.assert_awaited_once_with(plan_file)

    @staticmethod
    async def test_nothing_given_and_no_plan_falls_back_to_prompt_review():
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            (working_dir / "docs").mkdir()
            with (
                patch("meow.review_cli.load_config", return_value=_config()),
                patch(
                    "meow.review_cli.ReviewerAgent.review_prompt",
                    new=AsyncMock(return_value=("PASS", "STATUS: PASS\n")),
                ) as mock_review,
            ):
                await review_cli.run_review_command(working_dir, None, fix=False)
            mock_review.assert_awaited_once_with(None)


class BranchSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_worktree_mode_report_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            worktree_dir = working_dir / ".worktrees" / "branch-review-feature-x"
            with (
                patch("meow.review_cli.load_config", return_value=_config()),
                patch(
                    "meow.review_cli._ensure_existing_branch_worktree",
                    return_value=worktree_dir,
                ) as mock_ensure,
                patch(
                    "meow.review_cli.ReviewerAgent.review_branch",
                    new=AsyncMock(return_value=("FAIL", "STATUS: FAIL\n")),
                ) as mock_review,
                patch("meow.orchestrator.ReviewFixAgent") as mock_fixer_cls,
            ):
                result = await review_cli.run_review_command(
                    working_dir, None, fix=False, branch="feature/x", target="main"
                )
            self.assertIsNone(result)
            mock_ensure.assert_called_once_with(
                working_dir, "branch-review-feature-x", "feature/x"
            )
            mock_review.assert_awaited_once_with("main", "feature/x")
            mock_fixer_cls.assert_not_called()

    async def test_no_worktree_requires_branch_checked_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            with (
                patch("meow.review_cli.load_config", return_value=_config()),
                patch(
                    "meow.review_cli._require_branch_checked_out",
                    side_effect=RuntimeError("wrong branch"),
                ) as mock_require,
                self.assertRaisesRegex(RuntimeError, "wrong branch"),
            ):
                await review_cli.run_review_command(
                    working_dir,
                    None,
                    fix=False,
                    branch="feature/x",
                    target="main",
                    use_worktree=False,
                )
            mock_require.assert_called_once_with(working_dir, "feature/x")

    async def test_fix_raises_on_exhaustion(self):
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            with (
                patch("meow.review_cli.load_config", return_value=_config()),
                patch("meow.review_cli._require_branch_checked_out"),
                patch(
                    "meow.review_cli.ReviewerAgent.review_branch",
                    new=AsyncMock(return_value=("FAIL", "STATUS: FAIL\n")),
                ),
                self.assertRaisesRegex(RuntimeError, "did not pass"),
            ):
                await review_cli.run_review_command(
                    working_dir,
                    None,
                    fix=True,
                    branch="feature/x",
                    target="main",
                    use_worktree=False,
                )


class GitlabSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_only_fetches_and_reviews(self):
        config = _config(gitlab={"mcp": {"command": "uvx", "args": []}})
        mr = {"title": "t", "description": "d", "diff": "diff"}
        with (
            patch("meow.review_cli.load_config", return_value=config),
            patch(
                "meow.review_cli._fetch_merge_request", new=AsyncMock(return_value=mr)
            ) as mock_fetch,
            patch(
                "meow.review_cli.ReviewerAgent.review_merge_request",
                new=AsyncMock(return_value=("PASS", "STATUS: PASS\n")),
            ) as mock_review,
        ):
            result = await review_cli.run_review_command(
                Path("/project"), None, fix=False, gitlab_link="https://x/mr/1"
            )
        self.assertIsNone(result)
        mock_fetch.assert_awaited_once()
        mock_review.assert_awaited_once_with("t", "d", "diff")


class JiraSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_issue_and_reviews_as_prompt(self):
        config = _config(jira={"project_key": "PROJ", "mcp": {"command": "uvx"}})
        issue = {"key": "PROJ-1", "summary": "Add X", "description": "Details."}
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            with (
                patch("meow.review_cli.load_config", return_value=config),
                patch(
                    "meow.review_cli._fetch_issue", new=AsyncMock(return_value=issue)
                ) as mock_fetch,
                patch(
                    "meow.review_cli.ReviewerAgent.review_prompt",
                    new=AsyncMock(return_value=("PASS", "STATUS: PASS\n")),
                ) as mock_review,
            ):
                await review_cli.run_review_command(
                    working_dir, None, fix=False, jira_key="PROJ-1"
                )
            mock_fetch.assert_awaited_once()
            prompt = mock_review.await_args.args[0]
            self.assertIn("PROJ-1", prompt)
            self.assertIn("Add X", prompt)
            self.assertIn("Details.", prompt)


class ResumeReviewFileTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_flavor_raises_when_the_plan_file_is_gone(self):
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            docs_dir = working_dir / "docs"
            docs_dir.mkdir()
            review_file = docs_dir / "feature-review.md"
            review_file.write_text("STATUS: FAIL", encoding="utf-8")
            with (
                patch("meow.review_cli.load_config", return_value=_config()),
                self.assertRaisesRegex(FileNotFoundError, "feature.md"),
            ):
                await review_cli.run_review_command(
                    working_dir, None, fix=False, review_file=review_file
                )

    async def test_gitlab_flavor_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            docs_dir = working_dir / "docs"
            docs_dir.mkdir()
            review_file = docs_dir / "gitlab-review.md"
            review_file.write_text("STATUS: FAIL", encoding="utf-8")
            with (
                patch("meow.review_cli.load_config", return_value=_config()),
                self.assertRaisesRegex(RuntimeError, "no local checkout"),
            ):
                await review_cli.run_review_command(
                    working_dir, None, fix=False, review_file=review_file
                )

    async def test_branch_flavor_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            docs_dir = working_dir / "docs"
            docs_dir.mkdir()
            review_file = docs_dir / "branch-review.md"
            review_file.write_text("STATUS: FAIL", encoding="utf-8")
            with (
                patch("meow.review_cli.load_config", return_value=_config()),
                self.assertRaisesRegex(RuntimeError, "doesn't know the target branch"),
            ):
                await review_cli.run_review_command(
                    working_dir, None, fix=False, review_file=review_file
                )

    async def test_plan_flavor_resumes_with_focus(self):
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            docs_dir = working_dir / "docs"
            docs_dir.mkdir()
            plan_file = docs_dir / "feature.md"
            plan_file.write_text("# plan", encoding="utf-8")
            review_file = docs_dir / "feature-review.md"
            review_file.write_text(
                "SUMMARY: needs work\nSTATUS: FAIL\ncriterion: FAIL",
                encoding="utf-8",
            )
            with (
                patch("meow.review_cli.load_config", return_value=_config()),
                patch("meow.orchestrator.Generator") as mock_generator_cls,
                patch(
                    "meow.orchestrator.ReviewerAgent.review_plan",
                    new=AsyncMock(return_value=("PASS", "STATUS: PASS\n")),
                ) as mock_review,
            ):
                mock_generator = mock_generator_cls.return_value
                mock_generator.__aenter__ = AsyncMock(return_value=mock_generator)
                mock_generator.__aexit__ = AsyncMock(return_value=False)
                mock_generator.implement = AsyncMock(return_value="")

                result = await review_cli.run_review_command(
                    working_dir,
                    "focus on errors",
                    fix=False,
                    review_file=review_file,
                )
            self.assertIsNone(result)
            mock_review.assert_awaited_once_with(plan_file, focus="focus on errors")

    @staticmethod
    async def test_prompt_flavor_resumes():
        with tempfile.TemporaryDirectory() as tmp:
            working_dir = Path(tmp)
            docs_dir = working_dir / "docs"
            docs_dir.mkdir()
            review_file = docs_dir / "review.md"
            review_file.write_text(
                "SUMMARY: needs work\nSTATUS: FAIL\nrequirement: FAIL",
                encoding="utf-8",
            )
            with (
                patch("meow.review_cli.load_config", return_value=_config()),
                patch(
                    "meow.orchestrator.ReviewFixAgent"
                ) as mock_fixer_cls,
                patch(
                    "meow.orchestrator.ReviewerAgent.review_prompt",
                    new=AsyncMock(return_value=("PASS", "STATUS: PASS\n")),
                ) as mock_review,
            ):
                mock_fixer = mock_fixer_cls.return_value
                mock_fixer.__aenter__ = AsyncMock(return_value=mock_fixer)
                mock_fixer.__aexit__ = AsyncMock(return_value=False)
                mock_fixer.fix = AsyncMock(return_value="")

                await review_cli.run_review_command(
                    working_dir, None, fix=False, review_file=review_file
                )
            mock_review.assert_awaited_once_with(None)


if __name__ == "__main__":
    unittest.main()
