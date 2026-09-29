import unittest
from pathlib import Path

from meow import prompts
from meow.config import LintCommand


class PromptTests(unittest.TestCase):
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

    def test_worktree_hygiene_can_be_left_out(self):
        text = prompts.architecture_review_instructions(check_worktree_hygiene=False)

        self.assertNotIn("worktree", text)
        self.assertIn("SOLID/SRP", text)

    def test_prompt_review_switches_scope_on_empty_prompt(self):
        with_basis = prompts.prompt_review_prompt(
            "add csv", Path("review.md"), [], has_diff=True,
            docs_dir="docs", check_worktree_hygiene=False,
        )
        without = prompts.prompt_review_prompt(
            "", Path("review.md"), [], has_diff=False,
            docs_dir="docs", check_worktree_hygiene=False,
        )

        self.assertIn("'add csv'", with_basis)
        self.assertIn("git diff is empty", without)
        self.assertIn("Do not review anything under 'docs'", without)

    def test_mr_review_prompt_forbids_running_lint(self):
        text = prompts.mr_review_prompt(Path("gitlab-review.md"), check_worktree_hygiene=False)

        self.assertIn("Do not run or reference the project's lint commands", text)
        self.assertIn("one line per concern", text)
