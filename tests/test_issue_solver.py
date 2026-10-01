import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from meow import issue_solver


class LoadJiraConfigTests(unittest.TestCase):
    def test_raises_when_jira_table_is_missing(self):
        with self.assertRaisesRegex(ValueError, r"No \[jira\] table"):
            issue_solver._load_jira_config({})

    def test_raises_when_project_key_is_missing(self):
        with self.assertRaisesRegex(ValueError, "project_key"):
            issue_solver._load_jira_config({"jira": {"mcp": {"command": "uvx"}}})

    def test_raises_when_mcp_table_or_command_is_missing(self):
        with self.assertRaisesRegex(ValueError, r"\[jira\.mcp\]"):
            issue_solver._load_jira_config({"jira": {"project_key": "PROJ"}})

    def test_returns_normalized_config_with_default_branch_prefix(self):
        config = {
            "jira": {
                "project_key": "PROJ",
                "mcp": {"command": "uvx", "args": ["mcp-atlassian"]},
            }
        }

        result = issue_solver._load_jira_config(config)

        self.assertEqual(
            result,
            {
                "project_key": "PROJ",
                "branch_prefix": "issue/",
                "mcp": {"command": "uvx", "args": ["mcp-atlassian"]},
            },
        )

    def test_honors_a_configured_branch_prefix(self):
        config = {
            "jira": {
                "project_key": "PROJ",
                "branch_prefix": "fix/",
                "mcp": {"command": "uvx"},
            }
        }

        result = issue_solver._load_jira_config(config)

        self.assertEqual(result["branch_prefix"], "fix/")
        self.assertEqual(result["mcp"]["args"], [])


class SanitizeTests(unittest.TestCase):
    def test_issue_key_passes_through_unchanged(self):
        self.assertEqual(issue_solver._sanitize("PROJ-123"), "PROJ-123")

    def test_unsafe_characters_collapse_to_a_single_dash(self):
        self.assertEqual(issue_solver._sanitize("PROJ 123 / test"), "PROJ-123-test")


class RunIssueSolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_jira_test_failure_is_passed_into_sprint_and_never_pushes(self):
        working_dir = Path("/project")
        config = {
            "jira": {
                "project_key": "PROJ",
                "mcp": {"command": "uvx", "args": []},
            }
        }
        issue = {"key": "PROJ-2", "summary": "Fix thing", "description": "Details."}
        with (
            patch("meow.issue_solver.load_config", return_value=config),
            patch("meow.issue_solver._fetch_issue", new=AsyncMock(return_value=issue)),
            patch(
                "meow.issue_solver._ensure_branch_worktree",
                return_value=Path("/project/wt"),
            ),
            patch(
                "meow.issue_solver.run_sprint",
                new=AsyncMock(side_effect=RuntimeError("Tester stage failed")),
            ) as sprint,
            patch("meow.issue_solver._push_branch") as push,
            self.assertRaisesRegex(
                issue_solver.IssueUnresolvedError, "Tester stage failed"
            ),
        ):
            await issue_solver.run_issue_solver(working_dir, "PROJ-2", test=True)
        self.assertTrue(sprint.await_args.kwargs["test"])
        push.assert_not_called()

    async def test_fetches_solves_and_pushes_then_returns_issue_and_branch(self):
        working_dir = Path("/project")
        config = {
            "jira": {
                "project_key": "PROJ",
                "mcp": {"command": "uvx", "args": []},
            }
        }
        issue = {"key": "PROJ-1", "summary": "Fix thing", "description": "Details."}

        with (
            patch("meow.issue_solver.load_config", return_value=config),
            patch(
                "meow.issue_solver._fetch_issue", new=AsyncMock(return_value=issue)
            ) as mock_fetch,
            patch(
                "meow.issue_solver._ensure_branch_worktree",
                return_value=Path("/project/.worktrees/issue-proj-1"),
            ) as mock_worktree,
            patch("meow.issue_solver.run_sprint", new=AsyncMock()) as mock_sprint,
            patch("meow.issue_solver._push_branch") as mock_push,
        ):
            result = await issue_solver.run_issue_solver(working_dir, "PROJ-1")

        mock_fetch.assert_awaited_once_with(
            working_dir,
            config,
            {
                "project_key": "PROJ",
                "branch_prefix": "issue/",
                "mcp": {"command": "uvx", "args": []},
            },
            "PROJ-1",
        )
        mock_worktree.assert_called_once_with(
            working_dir, "issue-proj-1", "issue/PROJ-1"
        )
        mock_sprint.assert_awaited_once_with(
            Path("/project/.worktrees/issue-proj-1"),
            "issue-proj-1",
            "Resolve Jira issue PROJ-1: Fix thing\n\nDetails.",
            use_worktree=False,
            approve_plan=None,
        )
        mock_push.assert_called_once_with(
            Path("/project/.worktrees/issue-proj-1"), "issue/PROJ-1"
        )
        self.assertEqual(result, {"issue": "PROJ-1", "branch": "issue/PROJ-1"})

    async def test_passes_approve_plan_through_to_run_sprint(self):
        working_dir = Path("/project")
        config = {
            "jira": {
                "project_key": "PROJ",
                "mcp": {"command": "uvx", "args": []},
            }
        }
        issue = {"key": "PROJ-1", "summary": "Fix thing", "description": "Details."}
        mock_approve = MagicMock(return_value=True)

        with (
            patch("meow.issue_solver.load_config", return_value=config),
            patch("meow.issue_solver._fetch_issue", new=AsyncMock(return_value=issue)),
            patch(
                "meow.issue_solver._ensure_branch_worktree",
                return_value=Path("/project/.worktrees/issue-proj-1"),
            ),
            patch("meow.issue_solver.run_sprint", new=AsyncMock()) as mock_sprint,
            patch("meow.issue_solver._push_branch"),
        ):
            result = await issue_solver.run_issue_solver(
                working_dir, "PROJ-1", approve_plan=mock_approve
            )

        self.assertEqual(result, {"issue": "PROJ-1", "branch": "issue/PROJ-1"})
        mock_sprint.assert_awaited_once_with(
            Path("/project/.worktrees/issue-proj-1"),
            "issue-proj-1",
            "Resolve Jira issue PROJ-1: Fix thing\n\nDetails.",
            use_worktree=False,
            approve_plan=mock_approve,
        )

    async def test_raises_before_fetching_when_jira_config_is_missing(self):
        with (
            patch("meow.issue_solver.load_config", return_value={}),
            patch("meow.issue_solver._fetch_issue", new=AsyncMock()) as mock_fetch,
            self.assertRaisesRegex(ValueError, r"No \[jira\] table"),
        ):
            await issue_solver.run_issue_solver(Path("/project"))

        mock_fetch.assert_not_awaited()
