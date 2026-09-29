"""Reviewer agent setup, review context, and verdict parsing."""

import re
import shutil
import subprocess
from pathlib import Path

from meow.agents.base import Agent, AgentContext
from meow.prompts import (
    architecture_review_instructions,
    mr_review_prompt,
    no_prompt_review_instructions,
    plan_review_prompt,
    prompt_review_prompt,
)
from meow.sprint import Sprint

PROMPT_REVIEW_FILENAME = "review.md"
MR_REVIEW_FILENAME = "gitlab-review.md"

# Kept under its old private name for callers that reach it through this module.
_architecture_review_instructions = architecture_review_instructions
_no_prompt_review_instructions = no_prompt_review_instructions


def _git_review_context(context: AgentContext) -> tuple[str, bool]:
    """Capture the active worktree and original working-directory status."""
    git = shutil.which("git")
    if not git:
        return (
            "git is not installed or not on PATH; cannot inspect the working tree.",
            False,
        )

    active_dir = context.active_working_dir()
    status = subprocess.run(
        [git, "-C", str(active_dir), "status", "--short", "--branch"],
        check=False,
        capture_output=True,
        text=True,
    )
    diff = subprocess.run(
        [git, "-C", str(active_dir), "diff", "--"],
        check=False,
        capture_output=True,
        text=True,
    )
    diff_text = diff.stdout.strip()
    review_context = (
        "Git status for the active worktree:\n"
        f"{status.stdout.strip() or '(no git status output)'}\n\n"
        "Git diff for the active worktree:\n"
        f"{diff_text or '(no diff output)'}"
    )

    if active_dir != context.repo_dir:
        repo_status = subprocess.run(
            [git, "-C", str(context.repo_dir), "status", "--short", "--branch"],
            check=False,
            capture_output=True,
            text=True,
        )
        repo_diff = subprocess.run(
            [git, "-C", str(context.repo_dir), "diff", "--"],
            check=False,
            capture_output=True,
            text=True,
        )
        review_context += (
            "\n\nOriginal working-directory status (must be clean while a "
            f"worktree is active):\n{repo_status.stdout.strip() or '(no status)'}\n\n"
            "Original working-directory diff:\n"
            f"{repo_diff.stdout.strip() or '(no diff at original working directory)'}"
        )

    return review_context, bool(diff_text)


def _verdict_status(verdict_text: str) -> str:
    status_match = re.search(r"^STATUS:\s*(PASS|FAIL)", verdict_text, re.MULTILINE)
    return status_match.group(1) if status_match else "FAIL"


class ReviewerAgent(Agent):
    """Review plans and working trees through a generic project context."""

    async def review_prompt(self, prompt: str | None) -> tuple[str, str]:
        """Grade the working tree against a free-text prompt, or the git diff."""
        review_dir = self.context.active_working_dir() / self.context.config["docs_dir"]
        review_dir.mkdir(parents=True, exist_ok=True)
        review_file = review_dir / PROMPT_REVIEW_FILENAME
        review_basis = (prompt or "").strip()
        docs_dir = self.context.config["docs_dir"]
        git_context, has_diff = _git_review_context(self.context)
        options = self.options(
            system_prompt=prompt_review_prompt(
                review_basis,
                review_file,
                self.context.lint_commands(),
                has_diff=has_diff,
                docs_dir=docs_dir,
                check_worktree_hygiene=self.context.use_worktree,
            ),
            allowed_tools=["Read", "Grep", "Glob", "Bash", "Write"],
            role="reviewer",
            skills=["superpowers:verification-before-completion"],
        )
        query_prompt = (
            f"{no_prompt_review_instructions(has_diff=has_diff, docs_dir=docs_dir)}"
            f"\n\n{git_context}"
            if not review_basis
            else f"Review the prompt: {review_basis}\n\n{git_context}"
        )
        await self.run_query(query_prompt, options, "Reviewer")
        verdict_text = review_file.read_text()
        return _verdict_status(verdict_text), verdict_text

    async def review_merge_request(
        self, title: str, description: str, diff: str
    ) -> tuple[str, str]:
        """Grade a GitLab merge request's diff, independent of any local checkout.

        Mirrors `review_prompt`'s no-Sprint-Contract, report-only shape, but
        the source of truth is the merge request's title/description/diff
        handed in by the caller (fetched through a GitLab MCP server), not a
        local `git diff` -- the selected working directory need not be
        checked out at the merge request's commit, so lint commands and
        shell access are deliberately left out here; their result would not
        reflect this diff.
        """
        review_dir = self.context.active_working_dir() / self.context.config["docs_dir"]
        review_dir.mkdir(parents=True, exist_ok=True)
        review_file = review_dir / MR_REVIEW_FILENAME
        options = self.options(
            system_prompt=mr_review_prompt(
                review_file,
                check_worktree_hygiene=self.context.use_worktree,
            ),
            allowed_tools=["Read", "Grep", "Glob", "Write"],
            role="reviewer",
            skills=["superpowers:verification-before-completion"],
        )
        query_prompt = (
            f"Merge request title: {title}\n\n"
            f"Merge request description:\n{description}\n\n"
            f"Merge request diff:\n{diff}"
        )
        await self.run_query(query_prompt, options, "Reviewer")
        verdict_text = review_file.read_text()
        return _verdict_status(verdict_text), verdict_text

    async def review_plan(
        self, plan_file: Path, *, focus: str | None = None
    ) -> tuple[str, str]:
        """Grade a sprint plan against its contract and the current code.

        `focus`, when given, is appended as an extra instruction so the
        reviewer pays particular attention to it in addition to the Sprint
        Contract -- used by `meow review-fix-review` to carry its required
        prompt argument into every round's grading, not just the first.
        """
        review_file = plan_file.with_name(plan_file.stem + "-review.md")
        options = self.options(
            system_prompt=plan_review_prompt(
                plan_file,
                review_file,
                self.context.lint_commands(),
                focus=focus,
                check_worktree_hygiene=self.context.use_worktree,
            ),
            allowed_tools=["Read", "Grep", "Glob", "Bash", "Write"],
            role="reviewer",
            skills=["superpowers:verification-before-completion"],
        )
        await self.run_query(f"Review {plan_file}", options, "Reviewer")
        verdict_text = review_file.read_text()
        return _verdict_status(verdict_text), verdict_text


async def run_prompt_reviewer(
    sprint: Sprint, prompt: str | None
) -> tuple[str, str]:
    """Compatibility entry point for prompt reviews."""
    return await ReviewerAgent(sprint).review_prompt(prompt)


async def run_reviewer(sprint: Sprint, plan_file: Path) -> tuple[str, str]:
    """Compatibility entry point for sprint-plan reviews."""
    return await ReviewerAgent(sprint).review_plan(plan_file)
