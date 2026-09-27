import asyncio
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from meow import cli, orchestrator
from meow.agents import explorer as explorer_agent
from meow.agents import generator as generator_agent
from meow.agents import planner as planner_agent
from meow.agents import reviewer as reviewer_agent
from meow.sprint import Sprint


class CliCommandTests(unittest.TestCase):
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

    def test_feature_worktree_is_created_under_worktrees_by_default(self):
        project_root = Path("/tmp/project").resolve()
        worktree = orchestrator._ensure_feature_worktree(project_root, "ship-it")

        self.assertEqual(worktree, project_root / ".worktrees" / "ship-it")
        self.assertTrue(worktree.exists())

    @patch("meow.agents.planner.query")
    def test_agent_cwd_uses_worktree_root_when_present(self, mock_query):
        async def fake_query(*args, **kwargs):
            if False:
                yield None

        mock_query.side_effect = fake_query
        project_root = Path("/tmp/project").resolve()
        worktree_root = project_root / ".worktrees" / "ship-it"
        sprint = Sprint(
            repo_dir=project_root,
            config={
                "models": {"planner": "x", "generator": "x", "reviewer": "x", "explorer": "x"},
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

            orchestrator._boot_repo(project_root)

            self.assertEqual(
                (project_root / ".gitignore").read_text(encoding="utf-8"),
                "venv\n.worktrees/\n",
            )

    @staticmethod
    @patch("builtins.print")
    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_review", new_callable=AsyncMock)
    @patch("meow.cli.run_plan", new_callable=AsyncMock)
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_cli_logs_working_directory_once(
        mock_sprint, mock_plan, mock_review, mock_boot, mock_print
    ):
        with patch("sys.argv", ["meow", "review", "--work-dir", "."]):
            cli.cli_main()

        mock_print.assert_called_once_with(
            f"[meow] working directory: {Path('.').resolve()}"
        )
        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=False)
        mock_review.assert_awaited_once_with(Path(".").resolve(), None)
        mock_sprint.assert_not_called()
        mock_plan.assert_not_called()

    @staticmethod
    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_review", new_callable=AsyncMock)
    @patch("meow.cli.run_plan", new_callable=AsyncMock)
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_run_command_requires_worktree_when_not_disabled(
        mock_sprint, mock_plan, mock_review, mock_boot
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
        ):
            with self.assertRaises(SystemExit):
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

    @staticmethod
    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_review", new_callable=AsyncMock)
    @patch("meow.cli.run_plan", new_callable=AsyncMock)
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_plan_command_requires_worktree_when_not_disabled(
        mock_sprint, mock_plan, mock_review, mock_boot
    ):
        with patch("sys.argv", ["meow", "plan", "ship-it", "--work-dir", "."]):
            with self.assertRaises(SystemExit):
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

    @staticmethod
    @patch("meow.orchestrator.run_review", new_callable=AsyncMock)
    @patch("meow.orchestrator.run_plan", new_callable=AsyncMock)
    @patch("meow.orchestrator.run_sprint", new_callable=AsyncMock)
    def test_orchestrator_cli_prints_working_directory_for_every_invocation(
        mock_sprint, mock_plan, mock_review
    ):
        with (
            patch("builtins.print") as mock_print,
            patch("sys.argv", ["meow", "run", "ship-it", "--working-dir", ".", "--no-worktree"]),
        ):
            orchestrator.cli_main()

        self.assertTrue(
            any(
                call.args and str(Path(".").resolve()) in str(call.args[0])
                for call in mock_print.call_args_list
            )
        )
        mock_sprint.assert_awaited_once()
        mock_plan.assert_not_called()
        mock_review.assert_not_called()


class ArchitectureReviewInstructionsTests(unittest.TestCase):
    def test_instructs_reviewer_to_scan_docs_for_architecture_rules(self):
        instructions = reviewer_agent._architecture_review_instructions()

        self.assertIn("docs/", instructions)
        self.assertIn("Glob", instructions)
        self.assertNotIn("ARCHITECTURE.md", instructions)
        self.assertIn("FAIL", instructions)

    def test_review_education_requires_original_working_dir_to_stay_clean_when_worktree_is_used(self):
        instructions = reviewer_agent._architecture_review_instructions()

        self.assertIn("worktree", instructions)
        self.assertIn("main working directory", instructions)
        self.assertIn("clean", instructions)
        self.assertIn("FAIL", instructions)


class DocsScanInstructionTests(unittest.TestCase):
    def test_names_no_specific_filename(self):
        instruction = reviewer_agent._docs_scan_instruction("some purpose")

        self.assertIn("docs/", instruction)
        self.assertIn("Glob", instruction)
        self.assertIn("some purpose", instruction)
        self.assertNotIn(".md", instruction)


if __name__ == "__main__":
    unittest.main()
