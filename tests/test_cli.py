import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from meow import cli, orchestrator, worktree
from meow import sprint as sprint_module
from meow.agents import explorer as explorer_agent
from meow.agents import generator as generator_agent
from meow.agents import planner as planner_agent
from meow.agents import reviewer as reviewer_agent
from meow.agents.explorer import ExplorerAgent
from meow.agents.planner import PlannerAgent
from meow.agents.reviewer import ReviewerAgent
from meow.sprint import Sprint


class _LegacyExplorerContext:
    """Minimal `AgentContext` built straight from config and a directory.

    Mirrors what `_build_sprint` hands `make_explorer_agent`, independent of
    `explorer.py`'s own private `_LegacyContext`, so the comparison below
    doesn't just restate the production code under test.
    """

    def __init__(self, config: dict, working_dir: Path):
        self._config = config
        self._working_dir = working_dir

    def model(self, role: str) -> str | None:
        return self._config["models"][role]

    def active_working_dir(self) -> Path:
        return self._working_dir


class CliCommandTests(  # ruff: ignore[too-many-public-methods]
    unittest.TestCase
):
    # unittest gives every `test_*` a public method; splitting this fixture
    # class across files to satisfy max-public-methods would scatter closely
    # related CLI-dispatch coverage without adding clarity.
    def test_orchestrator_reuses_shared_role_implementations(self):
        self.assertIs(
            orchestrator.make_explorer_agent,
            explorer_agent.make_explorer_agent,
        )
        self.assertIs(orchestrator.run_planner, planner_agent.run_planner)
        self.assertIs(orchestrator.Generator, generator_agent.Generator)
        self.assertIs(
            orchestrator.run_prompt_reviewer,
            reviewer_agent.run_prompt_reviewer,
        )
        self.assertIs(orchestrator.run_reviewer, reviewer_agent.run_reviewer)

    def test_build_sprint_explorer_matches_explorer_agent_definition(self):
        config = {
            "models": {
                "planner": "x",
                "generator": "x",
                "reviewer": "x",
                "explorer": "x",
            },
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 1,
            "lint_timeout": 60,
        }
        working_dir = Path("/tmp/project").resolve()

        sprint = sprint_module.build_sprint(working_dir, config, working_dir)

        self.assertEqual(
            sprint.explorer,
            ExplorerAgent(_LegacyExplorerContext(config, working_dir)).definition(),
        )

    def test_run_sprint_drives_planner_and_reviewer_agent_classes(self):
        config = {
            "models": {
                "planner": "x",
                "generator": "x",
                "reviewer": "x",
                "explorer": "x",
            },
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 1,
            "lint_timeout": 60,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir)
            plan_file = working_dir / "docs" / "plan.md"

            mock_generator = MagicMock()
            mock_generator.__aenter__ = AsyncMock(return_value=mock_generator)
            mock_generator.__aexit__ = AsyncMock(return_value=False)
            mock_generator.implement = AsyncMock(return_value="")

            with (
                patch("meow.orchestrator.load_config", return_value=config),
                patch(
                    "meow.orchestrator.Generator", return_value=mock_generator
                ),
                patch.object(
                    PlannerAgent, "run", new_callable=AsyncMock
                ) as mock_planner_run,
                patch.object(
                    ReviewerAgent, "review_plan", new_callable=AsyncMock
                ) as mock_review_plan,
                patch(
                    "meow.orchestrator.run_planner", new_callable=AsyncMock
                ) as mock_run_planner,
                patch(
                    "meow.orchestrator.run_reviewer", new_callable=AsyncMock
                ) as mock_run_reviewer,
            ):
                mock_planner_run.return_value = plan_file
                mock_review_plan.return_value = ("PASS", "STATUS: PASS\n")

                asyncio.run(
                    orchestrator.run_sprint(
                        working_dir,
                        "ship-it",
                        "Add CSV export",
                        use_worktree=False,
                    )
                )

            self.assertEqual(mock_planner_run.await_count, 1)
            mock_review_plan.assert_awaited_once_with(plan_file)
            self.assertEqual(mock_run_planner.call_count, 0)
            self.assertEqual(mock_run_reviewer.call_count, 0)

    def test_no_prompt_review_falls_back_to_full_project_when_diff_is_empty(self):
        instructions = reviewer_agent._no_prompt_review_instructions(
            has_diff=False, docs_dir="docs/exec-plans/active"
        )

        self.assertIn("git diff is empty", instructions)
        self.assertIn("entire project", instructions)
        self.assertIn("docs/exec-plans/active", instructions)
        self.assertIn("Do not review", instructions)

    def test_no_prompt_review_uses_diff_when_diff_has_changes(self):
        instructions = reviewer_agent._no_prompt_review_instructions(
            has_diff=True, docs_dir="docs/exec-plans/active"
        )

        self.assertIn("git diff", instructions)
        self.assertNotIn("entire project", instructions)
        self.assertIn("docs/exec-plans/active", instructions)

    def test_feature_worktree_is_created_under_worktrees_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir).resolve()
            subprocess.run(
                ["git", "-C", str(project_root), "init", "-q"], check=True
            )
            subprocess.run(
                [
                    "git", "-C", str(project_root),
                    "-c", "user.email=test@example.com",
                    "-c", "user.name=test",
                    "commit", "--allow-empty", "-q", "-m", "init",
                ],
                check=True,
            )

            worktree_dir = worktree._ensure_feature_worktree(project_root, "ship-it")

            self.assertEqual(worktree_dir, project_root / ".worktrees" / "ship-it")
            self.assertTrue(worktree_dir.exists())

    def test_feature_worktree_creation_raises_when_git_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir).resolve()  # not a git repo

            with self.assertRaises(RuntimeError):
                worktree._ensure_feature_worktree(project_root, "ship-it")

    @patch("meow.agents.base.query")
    def test_agent_cwd_uses_worktree_root_when_present(self, mock_query):
        # Must stay async to match the SDK's async-generator `query` signature,
        # even though this stub never awaits or yields for real.
        async def fake_query(  # ruff: ignore[unused-async]
            *args, **kwargs
        ):
            if False:
                yield None

        mock_query.side_effect = fake_query
        project_root = Path("/tmp/project").resolve()
        worktree_root = project_root / ".worktrees" / "ship-it"
        sprint = Sprint(
            repo_dir=project_root,
            config={
                "models": {
                    "planner": "x",
                    "generator": "x",
                    "reviewer": "x",
                    "explorer": "x",
                },
                "lint": [],
                "docs_dir": "docs",
                "max_rounds": 1,
            },
            explorer=explorer_agent.make_explorer_agent(
                {"models": {"explorer": "x"}}, worktree_root
            ),
            lint_hook=None,
            working_dir=worktree_root,
            use_worktree=True,
        )

        async def _run():
            await planner_agent.run_planner(sprint, "ship-it", "Add CSV export")

        asyncio.run(_run())

        options = mock_query.call_args.kwargs["options"]
        self.assertEqual(options.cwd, str(worktree_root))

    def test_boot_repo_adds_worktrees_to_gitignore(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / ".gitignore").write_text("venv\n", encoding="utf-8")

            worktree._boot_repo(project_root)

            self.assertEqual(
                (project_root / ".gitignore").read_text(encoding="utf-8"),
                "venv\n.worktrees/\n",
            )

    @staticmethod
    def _init_repo(project_root: Path) -> None:
        subprocess.run(["git", "-C", str(project_root), "init", "-q"], check=True)
        subprocess.run(
            [
                "git", "-C", str(project_root),
                "-c", "user.email=test@example.com",
                "-c", "user.name=test",
                "commit", "--allow-empty", "-q", "-m", "init",
            ],
            check=True,
        )

    def test_ensure_branch_worktree_creates_a_real_pushable_branch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir).resolve()
            self._init_repo(project_root)

            worktree_dir = worktree._ensure_branch_worktree(
                project_root, "issue-proj-1", "issue/PROJ-1"
            )

            self.assertTrue(worktree_dir.is_dir())
            branch = subprocess.run(
                ["git", "-C", str(worktree_dir), "branch", "--show-current"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.assertEqual(branch, "issue/PROJ-1")

    def test_ensure_branch_worktree_reuses_an_existing_worktree_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir).resolve()
            self._init_repo(project_root)
            first = worktree._ensure_branch_worktree(
                project_root, "issue-proj-1", "issue/PROJ-1"
            )

            second = worktree._ensure_branch_worktree(
                project_root, "issue-proj-1", "issue/PROJ-1"
            )

            self.assertEqual(first, second)

    def test_push_branch_raises_a_clear_error_with_no_origin_remote(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir).resolve()
            self._init_repo(project_root)

            with self.assertRaisesRegex(RuntimeError, "No 'origin' remote"):
                worktree._push_branch(project_root, "issue/PROJ-1")

    # One stacked @patch per collaborator this test verifies is left alone;
    # trimming any would weaken the "every other command stays untouched"
    # assertions below.
    @staticmethod
    @patch("meow.orchestrator.logger")
    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_review", new_callable=AsyncMock)
    @patch("meow.cli.run_plan", new_callable=AsyncMock)
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_cli_logs_working_directory_once(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
        mock_sprint, mock_plan, mock_review, mock_boot, mock_logger
    ):
        with patch("sys.argv", ["meow", "review", "--work-dir", "."]):
            cli.cli_main()

        mock_logger.info.assert_called_once_with(
            "working_directory_resolved", path=str(Path(".").resolve())
        )
        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=False)
        mock_review.assert_awaited_once_with(Path(".").resolve(), None)
        mock_sprint.assert_not_called()
        mock_plan.assert_not_called()

    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_review", new_callable=AsyncMock)
    @patch("meow.cli.run_plan", new_callable=AsyncMock)
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_run_command_requires_worktree_when_not_disabled(
        self, mock_sprint, mock_plan, mock_review, mock_boot
    ):
        with patch(
            "sys.argv",
            [
                "meow",
                "run",
                "ship-it",
                "--work-dir",
                ".",
                "--plan",
                "docs/exec-plans/active/ship-it.md",
            ],
        ), self.assertRaises(SystemExit):
            cli.cli_main()

        mock_boot.assert_not_called()
        mock_sprint.assert_not_called()
        mock_plan.assert_not_called()
        mock_review.assert_not_called()

    @staticmethod
    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_review", new_callable=AsyncMock)
    @patch("meow.cli.run_plan", new_callable=AsyncMock)
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_run_command_accepts_word_flags_and_short_aliases(
        mock_sprint, mock_plan, mock_review, mock_boot
    ):
        with patch(
            "sys.argv",
            [
                "meow",
                "run",
                "ship-it",
                "--name",
                "case-123",
                "--work-dir",
                ".",
                "--plan",
                "docs/exec-plans/active/ship-it.md",
            ],
        ):
            cli.cli_main()

        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=True)
        mock_sprint.assert_awaited_once_with(
            Path(".").resolve(),
            "case-123",
            "ship-it",
            use_worktree=True,
            plan_file=Path("docs/exec-plans/active/ship-it.md").resolve(),
        )
        mock_plan.assert_not_called()
        mock_review.assert_not_called()

    @staticmethod
    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_review", new_callable=AsyncMock)
    @patch("meow.cli.run_plan", new_callable=AsyncMock)
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_review_command_is_supported(
        mock_sprint, mock_plan, mock_review, mock_boot
    ):
        with patch("sys.argv", ["meow", "review", "--work-dir", "."]):
            cli.cli_main()

        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=False)
        mock_review.assert_awaited_once_with(Path(".").resolve(), None)
        mock_sprint.assert_not_called()
        mock_plan.assert_not_called()

    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_review", new_callable=AsyncMock)
    @patch("meow.cli.run_plan", new_callable=AsyncMock)
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_plan_command_requires_worktree_when_not_disabled(
        self, mock_sprint, mock_plan, mock_review, mock_boot
    ):
        with (
            patch("sys.argv", ["meow", "plan", "ship-it", "--work-dir", "."]),
            self.assertRaises(SystemExit),
        ):
            cli.cli_main()

        mock_boot.assert_not_called()
        mock_plan.assert_not_called()
        mock_review.assert_not_called()
        mock_sprint.assert_not_called()

    @staticmethod
    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_review", new_callable=AsyncMock)
    @patch("meow.cli.run_plan", new_callable=AsyncMock)
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_plan_command_is_supported(
        mock_sprint, mock_plan, mock_review, mock_boot
    ):
        with patch(
            "sys.argv",
            ["meow", "plan", "ship-it", "--name", "case-456", "--work-dir", "."],
        ):
            cli.cli_main()

        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=True)
        mock_plan.assert_awaited_once_with(
            Path(".").resolve(),
            "case-456",
            "ship-it",
            use_worktree=True,
        )
        mock_review.assert_not_called()
        mock_sprint.assert_not_called()

    @staticmethod
    def test_cr_command_is_supported():
        with (
            patch("meow.cli._boot_repo") as mock_boot,
            patch("meow.cli.run_prompt_review", new_callable=AsyncMock) as mock_cr,
            patch("meow.cli.run_review", new_callable=AsyncMock) as mock_review,
            patch("meow.cli.run_plan", new_callable=AsyncMock) as mock_plan,
            patch("meow.cli.run_sprint", new_callable=AsyncMock) as mock_sprint,
            patch(
                "sys.argv",
                ["meow", "cr", "add CSV export", "--work-dir", "."],
            ),
        ):
            cli.cli_main()

        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=False)
        mock_cr.assert_awaited_once_with(
            Path(".").resolve(),
            "add CSV export",
        )
        mock_review.assert_not_called()
        mock_plan.assert_not_called()
        mock_sprint.assert_not_called()

    @staticmethod
    def test_cr_command_without_prompt_uses_git_diff_review():
        with (
            patch("meow.cli._boot_repo") as mock_boot,
            patch("meow.cli.run_prompt_review", new_callable=AsyncMock) as mock_cr,
            patch("meow.cli.run_review", new_callable=AsyncMock) as mock_review,
            patch("meow.cli.run_plan", new_callable=AsyncMock) as mock_plan,
            patch("meow.cli.run_sprint", new_callable=AsyncMock) as mock_sprint,
            patch("sys.argv", ["meow", "cr", "--work-dir", "."]),
        ):
            cli.cli_main()

        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=False)
        mock_cr.assert_awaited_once_with(
            Path(".").resolve(),
            None,
        )
        mock_review.assert_not_called()
        mock_plan.assert_not_called()
        mock_sprint.assert_not_called()

    @staticmethod
    def test_issue_solver_command_is_supported():
        with (
            patch("meow.cli._boot_repo") as mock_boot,
            patch(
                "meow.cli.run_issue_solver", new_callable=AsyncMock
            ) as mock_issue_solver,
            patch("sys.argv", ["meow", "issue-solver", "PROJ-1", "--work-dir", "."]),
        ):
            mock_issue_solver.return_value = {
                "issue": "PROJ-1", "branch": "issue/PROJ-1"
            }
            cli.cli_main()

        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=True)
        mock_issue_solver.assert_awaited_once_with(Path(".").resolve(), "PROJ-1")

    @staticmethod
    def test_issue_solver_command_accepts_no_issue_key():
        with (
            patch("meow.cli._boot_repo"),
            patch(
                "meow.cli.run_issue_solver", new_callable=AsyncMock
            ) as mock_issue_solver,
            patch("sys.argv", ["meow", "issue-solver", "--work-dir", "."]),
        ):
            mock_issue_solver.return_value = {
                "issue": "PROJ-2", "branch": "issue/PROJ-2"
            }
            cli.cli_main()

        mock_issue_solver.assert_awaited_once_with(Path(".").resolve(), None)

    @staticmethod
    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_review", new_callable=AsyncMock)
    @patch("meow.cli.run_plan", new_callable=AsyncMock)
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_no_worktree_flag_skips_boot(
        mock_sprint, mock_plan, mock_review, mock_boot
    ):
        with patch(
            "sys.argv",
            ["meow", "run", "Change it", "--work-dir", ".", "--no-worktree"],
        ):
            cli.cli_main()

        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=False)
        mock_sprint.assert_awaited_once_with(
            Path(".").resolve(), None, "Change it", use_worktree=False, plan_file=None
        )


class ArchitectureReviewInstructionsTests(unittest.TestCase):
    def test_review_education_requires_original_working_dir_to_stay_clean_when_worktree_is_used(  # ruff: ignore[line-too-long]
        self,
    ):
        instructions = reviewer_agent._architecture_review_instructions()

        self.assertIn("worktree", instructions)
        self.assertIn("main working directory", instructions)
        self.assertIn("clean", instructions)
        self.assertIn("FAIL", instructions)

    def test_prompt_review_skips_worktree_hygiene_check(self):
        instructions = reviewer_agent._architecture_review_instructions(
            check_worktree_hygiene=False
        )

        self.assertNotIn("worktree", instructions)
        self.assertNotIn("main working directory", instructions)
        self.assertIn("SOLID/SRP", instructions)


class LatestPlanFileTests(unittest.TestCase):
    def test_ignores_crs_own_review_file_even_when_newest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            docs_dir = Path(tmpdir)
            plan_file = docs_dir / "plan.md"
            plan_file.write_text("# Plan", encoding="utf-8")
            review_file = docs_dir / "review.md"
            review_file.write_text("STATUS: PASS", encoding="utf-8")
            # Make the cr-produced review.md the most recently modified file,
            # the exact situation that used to make it look like "the plan".
            os.utime(review_file, (os.path.getatime(plan_file) + 10,) * 2)

            self.assertEqual(
                orchestrator._latest_plan_file(docs_dir), plan_file
            )

    def test_still_ignores_plan_review_verdicts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            docs_dir = Path(tmpdir)
            plan_file = docs_dir / "plan.md"
            plan_file.write_text("# Plan", encoding="utf-8")
            (docs_dir / "plan-review.md").write_text("STATUS: PASS", encoding="utf-8")

            self.assertEqual(
                orchestrator._latest_plan_file(docs_dir), plan_file
            )


if __name__ == "__main__":
    unittest.main()
