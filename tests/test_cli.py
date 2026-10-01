import asyncio
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from meow import cli, orchestrator, sprint_runner, worktree
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
                    sprint_runner.run_sprint(
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

    def test_run_sprint_raises_when_the_plan_is_not_approved(self):
        config = {
            "models": {
                "planner": "x", "generator": "x", "reviewer": "x", "explorer": "x",
            },
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 1,
            "lint_timeout": 60,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir)
            plan_file = working_dir / "docs" / "plan.md"
            mock_approve = MagicMock(return_value=False)

            with (
                patch("meow.orchestrator.load_config", return_value=config),
                patch("meow.orchestrator.Generator") as mock_generator_cls,
                patch.object(
                    PlannerAgent, "run", new_callable=AsyncMock
                ) as mock_planner_run,
            ):
                mock_planner_run.return_value = plan_file

                with self.assertRaisesRegex(
                    orchestrator.PlanNotApprovedError, "was not approved"
                ):
                    asyncio.run(
                        sprint_runner.run_sprint(
                            working_dir,
                            "ship-it",
                            "Add CSV export",
                            use_worktree=False,
                            approve_plan=mock_approve,
                        )
                    )

            mock_approve.assert_called_once_with(plan_file)
            mock_generator_cls.assert_not_called()

    def test_run_sprint_proceeds_when_the_plan_is_approved(self):
        config = {
            "models": {
                "planner": "x", "generator": "x", "reviewer": "x", "explorer": "x",
            },
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 1,
            "lint_timeout": 60,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir)
            plan_file = working_dir / "docs" / "plan.md"
            mock_approve = MagicMock(return_value=True)

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
            ):
                mock_planner_run.return_value = plan_file
                mock_review_plan.return_value = ("PASS", "STATUS: PASS\n")

                result = asyncio.run(
                    sprint_runner.run_sprint(
                        working_dir,
                        "ship-it",
                        "Add CSV export",
                        use_worktree=False,
                        approve_plan=mock_approve,
                    )
                )

            self.assertIsNone(result)
            mock_approve.assert_called_once_with(plan_file)
            mock_generator.implement.assert_awaited_once()

    def test_run_sprint_resumes_at_review_without_rerunning_the_generator(self):
        config = {
            "models": {
                "planner": "x", "generator": "x", "reviewer": "x", "explorer": "x",
            },
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 1,
            "lint_timeout": 60,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir)
            plan_file = working_dir / "docs" / "plan.md"
            plan_file.parent.mkdir(parents=True)
            plan_file.write_text("# Plan", encoding="utf-8")

            with (
                patch("meow.orchestrator.load_config", return_value=config),
                patch("meow.orchestrator.Generator") as mock_generator_cls,
                patch.object(
                    ReviewerAgent, "review_plan", new_callable=AsyncMock
                ) as mock_review_plan,
                patch.object(
                    PlannerAgent, "run", new_callable=AsyncMock
                ) as mock_planner_run,
            ):
                mock_review_plan.return_value = ("PASS", "STATUS: PASS\n")

                result = asyncio.run(
                    sprint_runner.run_sprint(
                        working_dir,
                        "ship-it",
                        "Add CSV export",
                        use_worktree=False,
                        plan_file=plan_file,
                        resume_at="review",
                    )
                )

            self.assertIsNone(result)
            mock_review_plan.assert_awaited_once_with(plan_file, focus=None)
            mock_generator_cls.assert_not_called()
            mock_planner_run.assert_not_called()

    def test_run_sprint_resume_at_review_auto_detects_the_latest_plan(self):
        config = {
            "models": {
                "planner": "x", "generator": "x", "reviewer": "x", "explorer": "x",
            },
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 1,
            "lint_timeout": 60,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir)
            docs_dir = working_dir / "docs"
            docs_dir.mkdir()
            plan_file = docs_dir / "existing-plan.md"
            plan_file.write_text("# Plan", encoding="utf-8")

            with (
                patch("meow.orchestrator.load_config", return_value=config),
                patch("meow.orchestrator.Generator") as mock_generator_cls,
                patch.object(
                    ReviewerAgent, "review_plan", new_callable=AsyncMock
                ) as mock_review_plan,
            ):
                mock_review_plan.return_value = ("PASS", "STATUS: PASS\n")

                result = asyncio.run(
                    sprint_runner.run_sprint(
                        working_dir,
                        "ship-it",
                        "Add CSV export",
                        use_worktree=False,
                        resume_at="review",
                    )
                )

            self.assertIsNone(result)
            mock_review_plan.assert_awaited_once_with(plan_file, focus=None)
            mock_generator_cls.assert_not_called()

    def test_run_sprint_resume_at_review_still_gates_on_approval(self):
        config = {
            "models": {
                "planner": "x", "generator": "x", "reviewer": "x", "explorer": "x",
            },
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 1,
            "lint_timeout": 60,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir)
            plan_file = working_dir / "docs" / "plan.md"
            plan_file.parent.mkdir(parents=True)
            plan_file.write_text("# Plan", encoding="utf-8")
            mock_approve = MagicMock(return_value=False)

            with (
                patch("meow.orchestrator.load_config", return_value=config),
                patch("meow.orchestrator.Generator") as mock_generator_cls,
                patch.object(
                    ReviewerAgent, "review_plan", new_callable=AsyncMock
                ) as mock_review_plan,
                self.assertRaises(orchestrator.PlanNotApprovedError),
            ):
                asyncio.run(
                    sprint_runner.run_sprint(
                        working_dir,
                        "ship-it",
                        "Add CSV export",
                        use_worktree=False,
                        plan_file=plan_file,
                        resume_at="review",
                        approve_plan=mock_approve,
                    )
                )

            mock_approve.assert_called_once_with(plan_file)
            mock_review_plan.assert_not_awaited()
            mock_generator_cls.assert_not_called()

    def test_run_sprint_rejects_an_invalid_resume_at_value(self):
        with self.assertRaisesRegex(ValueError, "resume_at"):
            asyncio.run(
                sprint_runner.run_sprint(
                    Path("/project"),
                    "ship-it",
                    "Add CSV export",
                    use_worktree=False,
                    resume_at="somewhere-else",
                )
            )

    def test_run_sprint_resume_at_review_raises_when_no_plan_exists_anywhere(self):
        config = {
            "models": {
                "planner": "x", "generator": "x", "reviewer": "x", "explorer": "x",
            },
            "lint": [],
            "docs_dir": "docs",
            "max_rounds": 1,
            "lint_timeout": 60,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            working_dir = Path(tmpdir)

            with (
                patch("meow.orchestrator.load_config", return_value=config),
                patch("meow.orchestrator.Generator") as mock_generator_cls,
                self.assertRaisesRegex(FileNotFoundError, "No plan file found"),
            ):
                asyncio.run(
                    sprint_runner.run_sprint(
                        working_dir,
                        "ship-it",
                        "Add CSV export",
                        use_worktree=False,
                        resume_at="review",
                    )
                )

            mock_generator_cls.assert_not_called()

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

    def test_feature_worktree_is_created_from_a_source_branch_when_given(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir).resolve()
            self._init_repo(project_root)
            subprocess.run(
                ["git", "-C", str(project_root), "branch", "other-branch"],
                check=True,
            )
            subprocess.run(
                [
                    "git", "-C", str(project_root),
                    "-c", "user.email=test@example.com",
                    "-c", "user.name=test",
                    "commit", "--allow-empty", "-q", "-m", "second commit",
                ],
                check=True,
            )

            worktree_dir = worktree._ensure_feature_worktree(
                project_root, "ship-it", source_branch="other-branch"
            )

            checked_out = subprocess.run(
                ["git", "-C", str(worktree_dir), "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            other_branch_commit = subprocess.run(
                ["git", "-C", str(project_root), "rev-parse", "other-branch"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            main_commit = subprocess.run(
                ["git", "-C", str(project_root), "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()

            self.assertEqual(checked_out, other_branch_commit)
            self.assertNotEqual(checked_out, main_commit)

    def test_feature_worktree_reuse_ignores_source_branch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir).resolve()
            self._init_repo(project_root)
            first = worktree._ensure_feature_worktree(project_root, "ship-it")
            subprocess.run(
                ["git", "-C", str(project_root), "branch", "other-branch"],
                check=True,
            )

            second = worktree._ensure_feature_worktree(
                project_root, "ship-it", source_branch="other-branch"
            )

            self.assertEqual(first, second)

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

    def test_is_linked_worktree_is_false_for_the_main_checkout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir).resolve()
            self._init_repo(project_root)

            self.assertFalse(worktree._is_linked_worktree(project_root))

    def test_is_linked_worktree_is_true_for_a_linked_worktree(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir).resolve()
            self._init_repo(project_root)
            worktree_dir = worktree._ensure_feature_worktree(
                project_root, "ship-it"
            )

            self.assertTrue(worktree._is_linked_worktree(worktree_dir))

    def test_ensure_clean_tree_ignores_uncommitted_changes_in_a_linked_worktree(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir).resolve()
            self._init_repo(project_root)
            worktree_dir = worktree._ensure_feature_worktree(
                project_root, "ship-it"
            )
            (worktree_dir / "wip.txt").write_text("partial run", encoding="utf-8")

            worktree._ensure_clean_tree(worktree_dir)  # must not raise

    def test_ensure_clean_tree_still_blocks_a_dirty_main_repo(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir).resolve()
            self._init_repo(project_root)
            (project_root / "dirty.txt").write_text("oops", encoding="utf-8")

            with self.assertRaises(worktree.DirtyWorkingTreeError):
                worktree._ensure_clean_tree(project_root)

    def test_ensure_clean_tree_ignores_boot_repos_own_gitignore_edit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir).resolve()
            self._init_repo(project_root)
            worktree._ensure_clean_tree(project_root)  # first invocation: passes
            worktree._boot_repo(project_root, include_gitignore=True)

            worktree._ensure_clean_tree(project_root)  # must not raise

    def test_ensure_clean_tree_still_blocks_other_changes_beside_gitignore(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir).resolve()
            self._init_repo(project_root)
            worktree._boot_repo(project_root, include_gitignore=True)
            (project_root / "dirty.txt").write_text("oops", encoding="utf-8")

            with self.assertRaisesRegex(
                worktree.DirtyWorkingTreeError, "2 uncommitted"
            ):
                worktree._ensure_clean_tree(project_root)

    def test_feature_worktree_reuses_an_existing_registered_worktree(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir).resolve()
            self._init_repo(project_root)
            first = worktree._ensure_feature_worktree(project_root, "ship-it")
            (first / "wip.txt").write_text("partial run", encoding="utf-8")

            second = worktree._ensure_feature_worktree(project_root, "ship-it")

            self.assertEqual(first, second)
            self.assertTrue((second / "wip.txt").exists())

    def test_resolve_working_dir_uses_an_existing_worktree_in_place(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir).resolve()
            self._init_repo(project_root)
            worktree_dir = worktree._ensure_feature_worktree(
                project_root, "ship-it"
            )
            (worktree_dir / "wip.txt").write_text("partial run", encoding="utf-8")

            active_dir, effective_name, is_worktree = worktree._resolve_working_dir(
                worktree_dir, use_worktree=True, feature_name="nested"
            )

            self.assertEqual(active_dir, worktree_dir)
            self.assertEqual(effective_name, "nested")
            self.assertFalse(is_worktree)
            self.assertFalse((worktree_dir / ".worktrees" / "nested").exists())

    def test_resolve_working_dir_passes_source_branch_through_to_worktree_creation(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir).resolve()
            self._init_repo(project_root)
            subprocess.run(
                ["git", "-C", str(project_root), "branch", "other-branch"],
                check=True,
            )
            subprocess.run(
                [
                    "git", "-C", str(project_root),
                    "-c", "user.email=test@example.com",
                    "-c", "user.name=test",
                    "commit", "--allow-empty", "-q", "-m", "second commit",
                ],
                check=True,
            )

            active_dir, _, is_worktree = worktree._resolve_working_dir(
                project_root,
                use_worktree=True,
                feature_name="ship-it",
                source_branch="other-branch",
            )

            self.assertTrue(is_worktree)
            checked_out = subprocess.run(
                ["git", "-C", str(active_dir), "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            other_branch_commit = subprocess.run(
                ["git", "-C", str(project_root), "rev-parse", "other-branch"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.assertEqual(checked_out, other_branch_commit)

    def test_feature_worktree_rejects_a_stray_unregistered_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir).resolve()
            self._init_repo(project_root)
            stray = project_root / ".worktrees" / "ship-it"
            stray.mkdir(parents=True)

            with self.assertRaisesRegex(RuntimeError, "not a registered git worktree"):
                worktree._ensure_feature_worktree(project_root, "ship-it")

    # One stacked @patch per collaborator this test verifies is left alone;
    # trimming any would weaken the "every other command stays untouched"
    # assertions below.
    @staticmethod
    @patch("meow.orchestrator.logger")
    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_review_command", new_callable=AsyncMock)
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
        mock_review.assert_awaited_once_with(
            Path(".").resolve(),
            None,
            fix=False,
            jira_key=None,
            gitlab_link=None,
            branch=None,
            target=None,
            plan_file=None,
            review_file=None,
            use_worktree=True,
        )
        mock_sprint.assert_not_called()
        mock_plan.assert_not_called()

    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_review_command", new_callable=AsyncMock)
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
    @patch("meow.cli._ensure_clean_tree")
    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_review_command", new_callable=AsyncMock)
    @patch("meow.cli.run_plan", new_callable=AsyncMock)
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_run_command_accepts_word_flags_and_short_aliases(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
        mock_sprint, mock_plan, mock_review, mock_boot, mock_clean_tree
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
            source_branch=None,
            approve_plan=None,
            resume_at="generate",
        )
        mock_plan.assert_not_called()
        mock_review.assert_not_called()

    @staticmethod
    @patch("meow.cli._ensure_clean_tree")
    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_source_branch_is_passed_through_and_skips_the_clean_tree_check(
        mock_sprint, mock_boot, mock_clean_tree
    ):
        with patch(
            "sys.argv",
            [
                "meow", "run", "ship-it",
                "--name", "case-123",
                "--source-branch", "release/1.0",
                "--work-dir", ".",
            ],
        ):
            cli.cli_main()

        mock_clean_tree.assert_not_called()
        mock_sprint.assert_awaited_once_with(
            Path(".").resolve(),
            "case-123",
            "ship-it",
            use_worktree=True,
            plan_file=None,
            source_branch="release/1.0",
            approve_plan=None,
            resume_at="generate",
        )

    @staticmethod
    @patch("meow.cli._ensure_clean_tree")
    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_source_branch_accepts_the_from_alias(
        mock_sprint, mock_boot, mock_clean_tree
    ):
        with patch(
            "sys.argv",
            ["meow", "run", "ship-it", "--name", "case-123", "--from", "release/1.0"],
        ):
            cli.cli_main()

        mock_sprint.assert_awaited_once_with(
            Path(".").resolve(),
            "case-123",
            "ship-it",
            use_worktree=True,
            plan_file=None,
            source_branch="release/1.0",
            approve_plan=None,
            resume_at="generate",
        )

    @staticmethod
    @patch("meow.cli._ensure_clean_tree")
    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_worktree_mode_without_source_branch_still_requires_a_clean_tree(
        mock_sprint, mock_boot, mock_clean_tree
    ):
        with patch(
            "sys.argv",
            ["meow", "run", "ship-it", "--name", "case-123", "--work-dir", "."],
        ):
            cli.cli_main()

        mock_clean_tree.assert_called_once_with(Path(".").resolve())
        mock_sprint.assert_awaited_once_with(
            Path(".").resolve(),
            "case-123",
            "ship-it",
            use_worktree=True,
            plan_file=None,
            source_branch=None,
            approve_plan=None,
            resume_at="generate",
        )

    @staticmethod
    @patch("meow.cli._ensure_clean_tree")
    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_source_branch_with_no_worktree_still_requires_a_clean_tree(
        mock_sprint, mock_boot, mock_clean_tree
    ):
        with patch(
            "sys.argv",
            [
                "meow", "run", "ship-it",
                "--source-branch", "release/1.0",
                "--no-worktree",
                "--work-dir", ".",
            ],
        ):
            cli.cli_main()

        mock_clean_tree.assert_called_once_with(Path(".").resolve())
        mock_sprint.assert_awaited_once_with(
            Path(".").resolve(),
            None,
            "ship-it",
            use_worktree=False,
            plan_file=None,
            source_branch="release/1.0",
            approve_plan=None,
            resume_at="generate",
        )

    @staticmethod
    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_review_command", new_callable=AsyncMock)
    @patch("meow.cli.run_plan", new_callable=AsyncMock)
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_review_fix_flag_is_passed_through(
        mock_sprint, mock_plan, mock_review, mock_boot
    ):
        with patch(
            "sys.argv", ["meow", "review", "--fix", "--work-dir", "."]
        ):
            cli.cli_main()

        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=False)
        mock_review.assert_awaited_once_with(
            Path(".").resolve(),
            None,
            fix=True,
            jira_key=None,
            gitlab_link=None,
            branch=None,
            target=None,
            plan_file=None,
            review_file=None,
            use_worktree=True,
        )
        mock_sprint.assert_not_called()
        mock_plan.assert_not_called()

    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_review_command", new_callable=AsyncMock)
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
    @patch("meow.cli.run_review_command", new_callable=AsyncMock)
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
            source_branch=None,
        )
        mock_review.assert_not_called()
        mock_sprint.assert_not_called()

    @staticmethod
    def test_review_prompt_source_is_supported():
        with (
            patch("meow.cli._boot_repo") as mock_boot,
            patch(
                "meow.cli.run_review_command", new_callable=AsyncMock
            ) as mock_review,
            patch("meow.cli.run_plan", new_callable=AsyncMock) as mock_plan,
            patch("meow.cli.run_sprint", new_callable=AsyncMock) as mock_sprint,
            patch(
                "sys.argv",
                ["meow", "review", "add CSV export", "--work-dir", "."],
            ),
        ):
            cli.cli_main()

        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=False)
        mock_review.assert_awaited_once_with(
            Path(".").resolve(),
            "add CSV export",
            fix=False,
            jira_key=None,
            gitlab_link=None,
            branch=None,
            target=None,
            plan_file=None,
            review_file=None,
            use_worktree=True,
        )
        mock_plan.assert_not_called()
        mock_sprint.assert_not_called()

    @staticmethod
    def test_jira_build_mode_is_supported():
        with (
            patch("meow.cli._ensure_clean_tree"),
            patch("meow.cli._boot_repo") as mock_boot,
            patch(
                "meow.cli.run_issue_solver", new_callable=AsyncMock
            ) as mock_issue_solver,
            patch("sys.argv", ["meow", "run", "--jira", "PROJ-1", "--work-dir", "."]),
        ):
            mock_issue_solver.return_value = {
                "issue": "PROJ-1", "branch": "issue/PROJ-1"
            }
            cli.cli_main()

        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=True)
        mock_issue_solver.assert_awaited_once_with(
            Path(".").resolve(), "PROJ-1", approve_plan=None
        )

    @staticmethod
    def test_jira_build_mode_accepts_no_issue_key():
        with (
            patch("meow.cli._ensure_clean_tree"),
            patch("meow.cli._boot_repo"),
            patch(
                "meow.cli.run_issue_solver", new_callable=AsyncMock
            ) as mock_issue_solver,
            patch("sys.argv", ["meow", "run", "--jira", "--work-dir", "."]),
        ):
            mock_issue_solver.return_value = {
                "issue": "PROJ-2", "branch": "issue/PROJ-2"
            }
            cli.cli_main()

        mock_issue_solver.assert_awaited_once_with(
            Path(".").resolve(), None, approve_plan=None
        )

    @staticmethod
    def test_review_gitlab_source_is_supported():
        with (
            patch("meow.cli._boot_repo") as mock_boot,
            patch(
                "meow.cli.run_review_command", new_callable=AsyncMock
            ) as mock_review,
            patch(
                "sys.argv",
                [
                    "meow", "review", "--gitlab",
                    "https://gitlab.example.com/group/project/-/merge_requests/1",
                    "--work-dir", ".",
                ],
            ),
        ):
            cli.cli_main()

        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=False)
        mock_review.assert_awaited_once_with(
            Path(".").resolve(),
            None,
            fix=False,
            jira_key=None,
            gitlab_link="https://gitlab.example.com/group/project/-/merge_requests/1",
            branch=None,
            target=None,
            plan_file=None,
            review_file=None,
            use_worktree=True,
        )

    @staticmethod
    def test_review_does_not_require_clean_tree():
        with (
            patch("meow.cli._ensure_clean_tree") as mock_clean_tree,
            patch("meow.cli._boot_repo"),
            patch("meow.cli.run_review_command", new_callable=AsyncMock),
            patch(
                "sys.argv",
                ["meow", "review", "--gitlab", "https://gitlab.example.com/mr/1"],
            ),
        ):
            cli.cli_main()

        mock_clean_tree.assert_not_called()

    @staticmethod
    def test_lint_fix_mode_is_supported():
        with (
            patch("meow.cli._ensure_clean_tree"),
            patch("meow.cli._boot_repo") as mock_boot,
            patch(
                "meow.cli.run_lint_fix", new_callable=AsyncMock
            ) as mock_lint_fix,
            patch("sys.argv", ["meow", "run", "--lint-fix", "--work-dir", "."]),
        ):
            cli.cli_main()

        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=False)
        mock_lint_fix.assert_awaited_once_with(Path(".").resolve(), report_only=False)

    @staticmethod
    def test_lint_fix_mode_requires_clean_tree_by_default():
        with (
            patch("meow.cli._ensure_clean_tree") as mock_clean_tree,
            patch("meow.cli._boot_repo"),
            patch("meow.cli.run_lint_fix", new_callable=AsyncMock),
            patch("sys.argv", ["meow", "run", "--lint-fix", "--work-dir", "."]),
        ):
            cli.cli_main()

        mock_clean_tree.assert_called_once_with(Path(".").resolve())

    @staticmethod
    def test_lint_fix_report_only_skips_the_clean_tree_check():
        with (
            patch("meow.cli._ensure_clean_tree") as mock_clean_tree,
            patch("meow.cli._boot_repo"),
            patch(
                "meow.cli.run_lint_fix", new_callable=AsyncMock
            ) as mock_lint_fix,
            patch(
                "sys.argv",
                ["meow", "run", "--lint-fix", "--report-only", "--work-dir", "."],
            ),
        ):
            cli.cli_main()

        mock_clean_tree.assert_not_called()
        mock_lint_fix.assert_awaited_once_with(Path(".").resolve(), report_only=True)

    @staticmethod
    def test_lint_fix_report_only_prints_the_report_when_problems_remain():
        report_text = "$ ruff check\nF401 'os' imported but unused"
        buffer = io.StringIO()
        with (
            patch("meow.cli._ensure_clean_tree"),
            patch("meow.cli._boot_repo"),
            patch(
                "meow.cli.run_lint_fix",
                new_callable=AsyncMock,
                return_value=report_text,
            ),
            patch(
                "sys.argv",
                ["meow", "run", "--lint-fix", "--report-only", "--work-dir", "."],
            ),
            redirect_stdout(buffer),
        ):
            cli.cli_main()

        assert buffer.getvalue().strip() == report_text

    @staticmethod
    def test_lint_fix_report_only_prints_clean_when_nothing_remains():
        buffer = io.StringIO()
        with (
            patch("meow.cli._ensure_clean_tree"),
            patch("meow.cli._boot_repo"),
            patch(
                "meow.cli.run_lint_fix", new_callable=AsyncMock, return_value=None
            ),
            patch(
                "sys.argv",
                ["meow", "run", "--lint-fix", "--report-only", "--work-dir", "."],
            ),
            redirect_stdout(buffer),
        ):
            cli.cli_main()

        assert buffer.getvalue().strip() == "Lint is clean -- no issues found."

    @staticmethod
    @patch("meow.cli._ensure_clean_tree")
    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_review_command", new_callable=AsyncMock)
    @patch("meow.cli.run_plan", new_callable=AsyncMock)
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_no_worktree_flag_skips_boot(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
        mock_sprint, mock_plan, mock_review, mock_boot, mock_clean_tree
    ):
        with patch(
            "sys.argv",
            ["meow", "run", "Change it", "--work-dir", ".", "--no-worktree"],
        ):
            cli.cli_main()

        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=False)
        mock_sprint.assert_awaited_once_with(
            Path(".").resolve(),
            None,
            "Change it",
            use_worktree=False,
            plan_file=None,
            source_branch=None,
            approve_plan=None,
            resume_at="generate",
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


class PlanApprovalPromptTests(unittest.TestCase):
    """`_prompt_plan_approval` is the default `approve_plan` callback for
    --manually-approve-plan -- established here since no interactive-prompt
    testing pattern existed in this repo before: patch `meow.cli.input`
    (never real stdin) and capture stdout with `redirect_stdout` rather
    than asserting against the live console."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.plan_file = Path(self._tmpdir.name) / "plan.md"
        self.plan_file.write_text(
            "# Sprint Contract\n\n1. Do the thing.\n", encoding="utf-8"
        )

    def test_prints_the_plan_and_approves_on_y(self):
        out = io.StringIO()
        with (
            patch("meow.cli.input", return_value="y") as mock_input,
            redirect_stdout(out),
        ):
            approved = cli._prompt_plan_approval(self.plan_file)

        self.assertTrue(approved)
        self.assertIn("Do the thing.", out.getvalue())
        self.assertIn(str(self.plan_file), out.getvalue())
        mock_input.assert_called_once_with("Proceed with this plan? [y/N]: ")

    def test_accepts_yes_case_insensitively(self):
        with (
            patch("meow.cli.input", return_value="YES"),
            redirect_stdout(io.StringIO()),
        ):
            self.assertTrue(cli._prompt_plan_approval(self.plan_file))

    def test_declines_on_n(self):
        with patch("meow.cli.input", return_value="n"), redirect_stdout(io.StringIO()):
            self.assertFalse(cli._prompt_plan_approval(self.plan_file))

    def test_declines_on_empty_answer(self):
        with patch("meow.cli.input", return_value=""), redirect_stdout(io.StringIO()):
            self.assertFalse(cli._prompt_plan_approval(self.plan_file))

    def test_declines_on_eof_instead_of_raising(self):
        with (
            patch("meow.cli.input", side_effect=EOFError),
            redirect_stdout(io.StringIO()),
        ):
            self.assertFalse(cli._prompt_plan_approval(self.plan_file))


class ManuallyApprovePlanFlagTests(unittest.TestCase):
    """CLI-dispatch wiring for -m/--manually-approve-plan on plain build
    mode and --jira build mode -- `meow plan` never runs the generator, so
    it has no approval gate to offer and does not take this flag."""

    @patch("meow.cli._ensure_clean_tree")
    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_run_passes_the_prompt_callback_when_flag_is_given(
        self, mock_sprint, mock_boot, mock_clean_tree
    ):
        with patch(
            "sys.argv",
            ["meow", "run", "ship-it", "--name", "case-123", "-m"],
        ):
            cli.cli_main()

        self.assertEqual(mock_sprint.await_count, 1)
        mock_sprint.assert_awaited_once_with(
            Path(".").resolve(),
            "case-123",
            "ship-it",
            use_worktree=True,
            plan_file=None,
            source_branch=None,
            approve_plan=cli._prompt_plan_approval,
            resume_at="generate",
        )

    @patch("meow.cli._ensure_clean_tree")
    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_run_declining_the_plan_exits_cleanly_without_a_traceback(
        self, mock_sprint, mock_boot, mock_clean_tree
    ):
        mock_sprint.side_effect = orchestrator.PlanNotApprovedError(
            "Plan /tmp/plan.md was not approved -- stopping before the "
            "generator runs."
        )

        with (
            patch("sys.argv", ["meow", "run", "ship-it", "--name", "case-123", "-m"]),
            self.assertRaises(SystemExit) as ctx,
        ):
            cli.cli_main()

        self.assertEqual(ctx.exception.code, 1)

    def test_jira_build_passes_the_prompt_callback_when_flag_is_given(self):
        with (
            patch("meow.cli._ensure_clean_tree"),
            patch("meow.cli._boot_repo"),
            patch(
                "meow.cli.run_issue_solver", new_callable=AsyncMock
            ) as mock_issue_solver,
            patch(
                "sys.argv",
                ["meow", "run", "--jira", "PROJ-1", "--manually-approve-plan"],
            ),
        ):
            mock_issue_solver.return_value = {
                "issue": "PROJ-1", "branch": "issue/PROJ-1"
            }
            cli.cli_main()

        self.assertEqual(mock_issue_solver.await_count, 1)
        mock_issue_solver.assert_awaited_once_with(
            Path(".").resolve(), "PROJ-1", approve_plan=cli._prompt_plan_approval
        )

    def test_jira_build_declining_the_plan_exits_cleanly_via_issue_unresolved_error(
        self,
    ):
        from meow.issue_solver import IssueUnresolvedError

        with (
            patch("meow.cli._ensure_clean_tree"),
            patch("meow.cli._boot_repo"),
            patch(
                "meow.cli.run_issue_solver",
                new=AsyncMock(
                    side_effect=IssueUnresolvedError(
                        "Plan /tmp/plan.md was not approved -- stopping "
                        "before the generator runs."
                    )
                ),
            ),
            patch("sys.argv", ["meow", "run", "--jira", "PROJ-1", "-m"]),
            self.assertRaises(SystemExit) as ctx,
        ):
            cli.cli_main()

        self.assertEqual(ctx.exception.code, 1)

    def test_plan_command_does_not_accept_manually_approve_plan_flag(self):
        parser = cli._build_arg_parser()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["plan", "feature request", "-m"])


class ResumeAtFlagTests(unittest.TestCase):
    """CLI-dispatch wiring for --resume-at on plain build mode only --
    `meow plan` never runs the generator, so it has nothing to resume, and
    --jira/--lint-fix reject it outright (see the plan doc)."""

    @patch("meow.cli._ensure_clean_tree")
    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_resume_at_review_is_passed_through(
        self, mock_sprint, mock_boot, mock_clean_tree
    ):
        with patch(
            "sys.argv",
            ["meow", "run", "ship-it", "--name", "case-123", "--resume-at", "review"],
        ):
            cli.cli_main()

        self.assertEqual(mock_sprint.await_count, 1)
        mock_sprint.assert_awaited_once_with(
            Path(".").resolve(),
            "case-123",
            "ship-it",
            use_worktree=True,
            plan_file=None,
            source_branch=None,
            approve_plan=None,
            resume_at="review",
        )

    @patch("meow.cli._ensure_clean_tree")
    @patch("meow.cli._boot_repo")
    @patch("meow.cli.run_sprint", new_callable=AsyncMock)
    def test_resume_at_defaults_to_generate(
        self, mock_sprint, mock_boot, mock_clean_tree
    ):
        with patch("sys.argv", ["meow", "run", "ship-it", "--name", "case-123"]):
            cli.cli_main()

        self.assertEqual(mock_sprint.await_count, 1)
        mock_sprint.assert_awaited_once_with(
            Path(".").resolve(),
            "case-123",
            "ship-it",
            use_worktree=True,
            plan_file=None,
            source_branch=None,
            approve_plan=None,
            resume_at="generate",
        )

    def test_jira_build_rejects_resume_at_flag(self):
        parser = cli._build_arg_parser()
        args = parser.parse_args(
            ["run", "--jira", "PROJ-1", "--resume-at", "review"]
        )
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli._validate_run_flags(parser, args)

    def test_plan_command_does_not_accept_resume_at_flag(self):
        parser = cli._build_arg_parser()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["plan", "feature request", "--resume-at", "review"])


class ReviewDispatchTests(unittest.TestCase):
    """CLI-dispatch wiring for `meow review`'s --branch/--plan-file/
    --review-file sources -- --gitlab and the bare prompt/auto-discovery
    path are covered above; all five funnel through the same
    `run_review_command` call, checked here via its kwargs rather than
    five different functions' call signatures."""

    @staticmethod
    def test_branch_source_is_supported():
        with (
            patch("meow.cli._boot_repo") as mock_boot,
            patch(
                "meow.cli.run_review_command", new_callable=AsyncMock
            ) as mock_review,
            patch(
                "sys.argv",
                [
                    "meow", "review", "--branch", "feature/x",
                    "--target", "main", "--fix", "--work-dir", ".",
                ],
            ),
        ):
            cli.cli_main()

        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=True)
        mock_review.assert_awaited_once_with(
            Path(".").resolve(),
            None,
            fix=True,
            jira_key=None,
            gitlab_link=None,
            branch="feature/x",
            target="main",
            plan_file=None,
            review_file=None,
            use_worktree=True,
        )

    @staticmethod
    def test_branch_source_no_worktree_flag_skips_gitignore_boot():
        with (
            patch("meow.cli._boot_repo") as mock_boot,
            patch(
                "meow.cli.run_review_command", new_callable=AsyncMock
            ) as mock_review,
            patch(
                "sys.argv",
                [
                    "meow", "review", "--branch", "feature/x", "--target", "main",
                    "--no-worktree", "--work-dir", ".",
                ],
            ),
        ):
            cli.cli_main()

        mock_boot.assert_called_once_with(Path(".").resolve(), include_gitignore=False)
        mock_review.assert_awaited_once_with(
            Path(".").resolve(),
            None,
            fix=False,
            jira_key=None,
            gitlab_link=None,
            branch="feature/x",
            target="main",
            plan_file=None,
            review_file=None,
            use_worktree=False,
        )

    @staticmethod
    def test_plan_file_source_is_supported():
        with (
            patch("meow.cli._boot_repo"),
            patch(
                "meow.cli.run_review_command", new_callable=AsyncMock
            ) as mock_review,
            patch(
                "sys.argv",
                [
                    "meow", "review", "--fix",
                    "--plan-file", "docs/feature.md", "--work-dir", ".",
                ],
            ),
        ):
            cli.cli_main()

        mock_review.assert_awaited_once_with(
            Path(".").resolve(),
            None,
            fix=True,
            jira_key=None,
            gitlab_link=None,
            branch=None,
            target=None,
            plan_file=(Path(".") / "docs/feature.md").resolve(),
            review_file=None,
            use_worktree=True,
        )

    @staticmethod
    def test_review_file_source_is_supported():
        with (
            patch("meow.cli._boot_repo"),
            patch(
                "meow.cli.run_review_command", new_callable=AsyncMock
            ) as mock_review,
            patch(
                "sys.argv",
                [
                    "meow", "review", "--review-file", "docs/feature-review.md",
                    "Check error handling", "--work-dir", ".",
                ],
            ),
        ):
            cli.cli_main()

        mock_review.assert_awaited_once_with(
            Path(".").resolve(),
            "Check error handling",
            fix=False,
            jira_key=None,
            gitlab_link=None,
            branch=None,
            target=None,
            plan_file=None,
            review_file=(Path(".") / "docs/feature-review.md").resolve(),
            use_worktree=True,
        )

    def test_a_review_command_value_error_exits_cleanly_instead_of_a_traceback(self):
        # run_review_command validates its own source flags (e.g. --branch
        # without --target); this checks that _dispatch_review reports that
        # ValueError and exits 1 rather than letting it propagate raw.
        with (
            patch("meow.cli._boot_repo"),
            patch(
                "meow.cli.run_review_command",
                new=AsyncMock(
                    side_effect=ValueError(
                        "--branch and --target must be given together"
                    )
                ),
            ),
            patch(
                "sys.argv",
                ["meow", "review", "--branch", "feature/x", "--work-dir", "."],
            ),
            self.assertRaises(SystemExit) as ctx,
        ):
            cli.cli_main()

        self.assertEqual(ctx.exception.code, 1)


class RuntimeErrorExitsCleanlyTests(unittest.TestCase):
    """cli_main's last-resort net around `_dispatch`: a bare ValueError or
    RuntimeError from deep in run/plan/review (a malformed .harness.toml, a
    worktree/git failure, an unresumable review-file flavor, ...) must exit
    1 with its message, not propagate as a raw traceback. DirtyWorkingTreeError/
    PlanNotApprovedError/IssueUnresolvedError and review's ValueError are
    already covered by their own closer-to-the-source tests; these cover
    what reaching this outer net actually catches."""

    def test_plain_run_runtime_error_exits_cleanly(self):
        with (
            patch("meow.cli._boot_repo"),
            patch(
                "meow.cli.run_sprint",
                new=AsyncMock(
                    side_effect=RuntimeError(
                        "Failed to create worktree at .worktrees/x: fatal: ..."
                    )
                ),
            ),
            patch(
                "sys.argv",
                ["meow", "run", "do it", "--name", "x", "--work-dir", "."],
            ),
            self.assertRaises(SystemExit) as ctx,
        ):
            cli.cli_main()

        self.assertEqual(ctx.exception.code, 1)

    def test_plan_value_error_exits_cleanly(self):
        with (
            patch("meow.cli._boot_repo"),
            patch(
                "meow.cli.run_plan",
                new=AsyncMock(
                    side_effect=ValueError(
                        ".harness.toml: [[lint]] entry 1 must set 'command'"
                    )
                ),
            ),
            patch(
                "sys.argv",
                ["meow", "plan", "do it", "--name", "x", "--work-dir", "."],
            ),
            self.assertRaises(SystemExit) as ctx,
        ):
            cli.cli_main()

        self.assertEqual(ctx.exception.code, 1)

    def test_review_runtime_error_exits_cleanly(self):
        # Unlike the existing ValueError test above, this is run_review_command
        # raising a bare RuntimeError (e.g. _resume_review_file's unresumable-
        # flavor rejection) -- _dispatch_review only ever caught ValueError
        # itself, so this specifically exercises cli_main's outer net.
        with (
            patch("meow.cli._boot_repo"),
            patch(
                "meow.cli.run_review_command",
                new=AsyncMock(
                    side_effect=RuntimeError(
                        "branch-review.md is a branch review -- `meow review` "
                        "can't resume it from --review-file alone"
                    )
                ),
            ),
            patch(
                "sys.argv",
                ["meow", "review", "--review-file", "r.md", "--work-dir", "."],
            ),
            self.assertRaises(SystemExit) as ctx,
        ):
            cli.cli_main()

        self.assertEqual(ctx.exception.code, 1)

    def test_missing_harness_toml_exits_cleanly(self):
        # By far the most likely first mistake a new user makes: running
        # meow before creating .harness.toml at all. load_config raises
        # FileNotFoundError, a type cli_main's net didn't originally cover.
        with (
            patch("meow.cli._boot_repo"),
            patch(
                "meow.cli.run_plan",
                new=AsyncMock(
                    side_effect=FileNotFoundError(
                        "No .harness.toml found in /some/project. Create one "
                        "before running the harness."
                    )
                ),
            ),
            patch(
                "sys.argv",
                ["meow", "plan", "do it", "--name", "x", "--work-dir", "."],
            ),
            self.assertRaises(SystemExit) as ctx,
        ):
            cli.cli_main()

        self.assertEqual(ctx.exception.code, 1)

    def test_resume_at_review_with_no_plan_file_exits_cleanly(self):
        with (
            patch("meow.cli._boot_repo"),
            patch(
                "meow.cli.run_sprint",
                new=AsyncMock(
                    side_effect=FileNotFoundError(
                        "No plan file found in docs/exec-plans/active. Run "
                        '`meow plan "<feature>"` first, or pass --plan-file '
                        "explicitly."
                    )
                ),
            ),
            patch(
                "sys.argv",
                [
                    "meow", "run", "do it", "--resume-at", "review",
                    "--no-worktree", "--work-dir", ".",
                ],
            ),
            self.assertRaises(SystemExit) as ctx,
        ):
            cli.cli_main()

        self.assertEqual(ctx.exception.code, 1)


class FeatureNameRequirementTests(unittest.TestCase):
    """No test anywhere exercised _validate_feature_name_requirement before
    -- found while adversarially checking worktree-mode edge cases. Confirmed
    for real first: `meow run "..." --working-dir <path already a linked
    worktree>` (no --name, no --no-worktree) demanded --name anyway, even
    though _resolve_working_dir would have silently ignored it and used that
    directory in place regardless -- a --name with no effect, required for
    no reason."""

    def test_plain_run_without_name_or_no_worktree_is_rejected(self):
        with (
            patch("meow.cli._is_linked_worktree", return_value=False),
            patch("meow.cli._ensure_clean_tree"),
            patch("sys.argv", ["meow", "run", "do it", "--work-dir", "."]),
            self.assertRaises(SystemExit) as ctx,
        ):
            cli.cli_main()

        self.assertEqual(ctx.exception.code, 2)

    @staticmethod
    def test_plain_run_with_no_worktree_flag_does_not_require_a_name():
        with (
            patch("meow.cli._is_linked_worktree", return_value=False),
            patch("meow.cli._ensure_clean_tree"),
            patch("meow.cli._boot_repo"),
            patch("meow.cli.run_sprint", new_callable=AsyncMock) as mock_run,
            patch(
                "sys.argv",
                ["meow", "run", "do it", "--no-worktree", "--work-dir", "."],
            ),
        ):
            cli.cli_main()

        mock_run.assert_awaited_once()

    @staticmethod
    def test_working_dir_already_a_linked_worktree_does_not_require_a_name():
        # The fix: --working-dir pointing at an existing worktree means
        # "work here", same as --no-worktree would, so --name must not be
        # demanded just because that flag itself wasn't also passed.
        with (
            patch("meow.cli._is_linked_worktree", return_value=True),
            patch("meow.cli._ensure_clean_tree"),
            patch("meow.cli._boot_repo"),
            patch("meow.cli.run_sprint", new_callable=AsyncMock) as mock_run,
            patch("sys.argv", ["meow", "run", "do it", "--work-dir", "."]),
        ):
            cli.cli_main()

        mock_run.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
