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

    def test_worktree_bootstrap_instruction_uses_request_prompt_for_name(self):
        sprint = Sprint(
            repo_dir=Path("/tmp/project").resolve(),
            config={
                "models": {"planner": "x", "generator": "x", "reviewer": "x", "explorer": "x"},
                "lint": [],
                "docs_dir": "docs",
                "max_rounds": 1,
            },
            explorer=None,
            lint_hook=None,
            working_dir=None,
            use_worktree=True,
            worktree_name=None,
        )
        prompt = "Add CSV export"

        instruction = orchestrator._worktree_bootstrap_instruction(sprint, prompt)

        self.assertIn("Add CSV export", instruction)
        self.assertIn("normalize", instruction)
        self.assertIn("safe lowercase slug", instruction)

    def test_normalize_worktree_name_sanitizes_prompt_seed(self):
        self.assertEqual(orchestrator._normalize_worktree_name("Add CSV export"), "add-csv-export")
        self.assertEqual(orchestrator._normalize_worktree_name("---"), "feature")

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
            explorer=explorer_agent.make_explorer_agent({
                "models": {"explorer": "x"},
            }),
            lint_hook=None,
            working_dir=worktree_root,
            use_worktree=True,
            worktree_name="ship-it",
        )

        async def _run():
            await planner_agent.run_planner(sprint, "ship-it", "Add CSV export")

        asyncio.run(_run())

        options = mock_query.call_args.kwargs["options"]
        self.assertEqual(options.cwd, str(worktree_root))

    @patch("meow.orchestrator.run_planner", new_callable=AsyncMock)
    @patch("meow.orchestrator.load_config")
    @patch("meow.orchestrator._generate_worktree_name", new_callable=AsyncMock, return_value="ship-it")
    def test_run_plan_generates_worktree_name_via_name_helper(
        self, mock_name_gen, mock_load_config, mock_run_planner
    ):
        mock_load_config.return_value = {
            "models": {
                "explorer": "haiku",
                "planner": "sonnet",
                "generator": "sonnet",
                "reviewer": "sonnet",
            },
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 1,
        }

        asyncio.run(
            orchestrator.run_plan(
                Path("/tmp/project").resolve(),
                "feature",
                "Add CSV export",
                use_worktree=True,
                worktree_name=None,
            )
        )

        mock_name_gen.assert_awaited_once()
        self.assertEqual(mock_run_planner.call_args.args[1], "ship-it")

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
        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=True)
        mock_review.assert_awaited_once()
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
                "--worktree",
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
            worktree_name="case-123",
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

        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=True)
        mock_review.assert_awaited_once()
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
            ["meow", "plan", "ship-it", "--worktree", "case-456", "--work-dir", "."],
        ):
            cli.cli_main()

        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=True)
        mock_plan.assert_awaited_once_with(
            Path(".").resolve(),
            "case-456",
            "ship-it",
            use_worktree=True,
            worktree_name="case-456",
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

        mock_boot.assert_called_once_with(
            Path(".").resolve(), include_gitignore=True
        )
        mock_cr.assert_awaited_once_with(
            Path(".").resolve(),
            None,
            "add CSV export",
            use_worktree=True,
            worktree_name=None,
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

        mock_boot.assert_called_once_with(
            Path(".").resolve(), include_gitignore=True
        )
        mock_cr.assert_awaited_once_with(
            Path(".").resolve(),
            None,
            None,
            use_worktree=True,
            worktree_name=None,
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
            ["meow", "review", "--work-dir", ".", "--no-worktree"],
        ):
            cli.cli_main()

        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=False)
        mock_review.assert_awaited_once()

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
