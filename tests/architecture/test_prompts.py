import unittest
from pathlib import Path

from meow.project import prompts
from meow.project.config import LintCommand
from meow.project.shaping import ShapeContext


class PromptTests(unittest.TestCase):
    def test_shape_context_is_shared_by_planner_and_review(self):
        context = ShapeContext(
            "docs/shape.json", "bounded adapter", ("legacy API remains",)
        )
        planner = prompts.planner_prompt(Path("plan.md"), context)
        review = prompts.plan_review_prompt(
            Path("plan.md"),
            Path("review.md"),
            [],
            focus=None,
            check_worktree_hygiene=False,
            shape_context=context,
        )
        for text in (planner, review):
            self.assertIn("docs/shape.json", text)
            self.assertIn("legacy API remains", text)
            self.assertIn("Sprint Contract", text)

    def test_bug_planner_prompt_requires_reproduction_and_regression_evidence(self):
        text = prompts.planner_prompt(Path("plan.md"), bug_mode=True)

        self.assertIn("reproduce", text.lower())
        self.assertIn("regression", text.lower())
        self.assertIn("minimise", text.lower())

    def test_planner_prompt_decomposes_mixed_requests_into_graph_tasks(self):
        text = prompts.planner_prompt(Path("plan.md"))

        self.assertIn("distinct feature, bug fix, or validation outcome", text)
        self.assertIn("independent tasks", text)
        self.assertIn("shared files", text)
        self.assertIn("depends_on", text)
        self.assertIn("parallel", text.lower())

    def test_generator_prompt_respects_task_graph_boundaries(self):
        text = prompts.generator_prompt(Path("plan.md"))

        self.assertIn("tasks.json", text)
        self.assertIn("dependency order", text)
        self.assertIn("read-only exploration", text)
        self.assertIn("owned paths", text)

    def test_planner_and_reviewer_prompts_name_public_seams_and_boundaries(self):
        planner = prompts.planner_prompt(Path("plan.md"))
        review = prompts.plan_review_prompt(
            Path("plan.md"),
            Path("review.md"),
            [],
            focus=None,
            check_worktree_hygiene=False,
        )

        for text in (planner, review):
            self.assertIn("public seam", text)
            self.assertIn("module boundary", text)

    def test_plan_review_prompt_names_plan_verdict_and_lint_gates(self):
        text = prompts.plan_review_prompt(
            Path("p.md"),
            Path("p-review.md"),
            [LintCommand("ruff check"), LintCommand("fallow", gate=False)],
            focus="error handling",
            check_worktree_hygiene=True,
        )

        self.assertIn("Sprint Contract in p.md", text)
        self.assertIn("Pay particular attention to: error handling.", text)
        self.assertIn("FAIL criterion: `ruff check`", text)
        self.assertIn("do not fail the sprint on them", text)
        self.assertIn("Write your verdict to p-review.md", text)
        self.assertIn("worktree", text)
        self.assertIn("any language", text)
        self.assertIn("too many unrelated files", text)
        self.assertIn("arbitrary file-count", text)

    def test_worktree_hygiene_can_be_left_out(self):
        text = prompts.architecture_review_instructions(check_worktree_hygiene=False)

        self.assertNotIn("worktree", text)
        self.assertIn("SOLID/SRP", text)
        self.assertIn("dependency direction", text)

    def test_prompt_review_switches_scope_on_empty_prompt(self):
        with_basis = prompts.prompt_review_prompt(
            "add csv",
            Path("review.md"),
            [],
            has_diff=True,
            docs_dir="docs",
            check_worktree_hygiene=False,
        )
        without = prompts.prompt_review_prompt(
            "",
            Path("review.md"),
            [],
            has_diff=False,
            docs_dir="docs",
            check_worktree_hygiene=False,
        )

        self.assertIn("'add csv'", with_basis)
        self.assertIn("git diff is empty", without)
        self.assertIn("Do not review anything under 'docs'", without)

    def test_remote_review_prompt_forbids_running_lint(self):
        text = prompts.remote_review_prompt(
            Path("github-review.md"),
            "GitHub pull request",
            check_worktree_hygiene=False,
        )

        self.assertIn("Do not run or reference the project's lint commands", text)
        self.assertIn("one line per concern", text)

    def test_branch_review_prompt_names_target_and_branch_and_gates_lint(self):
        text = prompts.branch_review_prompt(
            "main",
            "feature/x",
            Path("branch-review.md"),
            [LintCommand("ruff check")],
            check_worktree_hygiene=True,
        )

        self.assertIn("'main'", text)
        self.assertIn("'feature/x'", text)
        self.assertIn("Write your verdict to branch-review.md", text)
        self.assertIn("FAIL criterion: `ruff check`", text)
        self.assertIn("worktree", text)
