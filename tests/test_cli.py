import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from meow import cli, orchestrator
from meow import roles as shared_roles


class CliCommandTests(unittest.TestCase):
    def test_orchestrator_reuses_shared_role_implementations(self):
        self.assertIs(
            orchestrator.make_explorer_agent,
            shared_roles.make_explorer_agent,
        )
        self.assertIs(orchestrator.run_planner, shared_roles.run_planner)
        self.assertIs(orchestrator.Generator, shared_roles.Generator)
        self.assertIs(
            orchestrator.run_prompt_reviewer,
            shared_roles.run_prompt_reviewer,
        )
        self.assertIs(orchestrator.run_reviewer, shared_roles.run_reviewer)

    @patch("meow.orchestrator.subprocess.run")
    def test_feature_worktree_is_created_under_worktrees_by_default(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "worktree", "add", "--detach"], returncode=0
        )

        project_root = Path("/tmp/project").resolve()
        worktree = orchestrator._ensure_feature_worktree(project_root, "ship-it")

        self.assertEqual(worktree, project_root / ".worktrees" / "ship-it")
        git_executable = shutil.which("git")
        mock_run.assert_called_once_with(
            [
                git_executable,
                "-C",
                str(project_root),
                "worktree",
                "add",
                "--detach",
                str(project_root / ".worktrees" / "ship-it"),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

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
    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_review", new_callable=AsyncMock)
    @patch("meow.cli.run_plan", new_callable=AsyncMock)
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_review_command_is_supported(
        mock_sprint, mock_plan, mock_review, mock_boot
    ):
        with patch("sys.argv", ["meow", "review", "--project-root", "."]):
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
    def test_plan_command_is_supported(
        mock_sprint, mock_plan, mock_review, mock_boot
    ):
        with patch("sys.argv", ["meow", "plan", "ship-it", "--project-root", "."]):
            cli.cli_main()

        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=True)
        mock_plan.assert_awaited_once()
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
                ["meow", "cr", "add CSV export", "--project-root", "."],
            ),
        ):
            cli.cli_main()

        mock_boot.assert_called_once_with(
            Path(".").resolve(), include_gitignore=True
        )
        mock_cr.assert_awaited_once_with(
            Path(".").resolve(),
            "add-csv-export",
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
            patch("sys.argv", ["meow", "cr", "--project-root", "."]),
        ):
            cli.cli_main()

        mock_boot.assert_called_once_with(
            Path(".").resolve(), include_gitignore=True
        )
        mock_cr.assert_awaited_once_with(
            Path(".").resolve(),
            "git-diff-review",
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
            ["meow", "review", "--project-root", ".", "--no-worktree"],
        ):
            cli.cli_main()

        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=False)
        mock_review.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
