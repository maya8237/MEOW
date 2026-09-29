"""Reviewer agent setup, review context, and verdict parsing."""

import re
import shutil
import subprocess
from pathlib import Path

from meow.agents.base import Agent, AgentContext
from meow.config import LintCommand
from meow.sprint import Sprint

PROMPT_REVIEW_FILENAME = "review.md"
MR_REVIEW_FILENAME = "gitlab-review.md"


def _lint_instructions(commands: list[LintCommand]) -> str:
    """Tell the reviewer which lint commands bind it and which only inform."""
    gates = [entry.command for entry in commands if entry.gate]
    non_blocking = [entry.command for entry in commands if not entry.gate]
    parts = []
    if gates:
        listed = ", ".join(f"`{command}`" for command in gates)
        parts.append(
            "Run each of these project-wide and treat any failure as a "
            f"FAIL criterion: {listed}."
        )
    if non_blocking:
        listed = ", ".join(f"`{command}`" for command in non_blocking)
        parts.append(
            f"Also run {listed} and summarise the findings in your review, "
            "but do not fail the sprint on them."
        )
    return " ".join(parts)


def _verification_instructions() -> str:
    """Require independent, evidence-based verdicts without editing code."""
    return (
        "Use verification-before-completion: base every PASS or FAIL on "
        "inspected code or observed command output; do not modify implementation. "
    )


def _architecture_review_instructions(*, check_worktree_hygiene: bool = True) -> str:
    """Tell the reviewer to look for monolithic, SRP-breaking modules."""
    instructions = (
        "Also perform a SOLID/SRP review. Flag any file or class that mixes "
        "multiple responsibilities, such as config parsing + agent wiring + "
        "lint hooks + orchestration + CLI handling in one module. Treat any "
        "single file that does more than one broad concern as a FAIL criterion "
        "unless the code is clearly split into cohesive helpers or classes. "
    )
    if check_worktree_hygiene:
        instructions += (
            "If the sprint used an isolated worktree, ensure every plan change "
            "stays inside the active worktree and that the main working "
            "directory remains clean; any edit there is a FAIL criterion. "
        )
    instructions += (
        "Use file:line evidence; do not accept 'it works' as an excuse for a "
        "monolithic design."
    )
    return instructions


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


def _no_prompt_review_instructions(*, has_diff: bool, docs_dir: str) -> str:
    """Select the review scope when the user did not supply a prompt."""
    exclusion = (
        f" Do not review anything under {docs_dir!r} -- that directory holds "
        "meow's own generated sprint plans and review verdicts, not project "
        "code, and grading them as if they were the project under review "
        "produces nonsensical meta-reviews."
    )
    if has_diff:
        return (
            "There is no explicit prompt and the git diff contains changes. "
            "Review the changes shown in the git diff, using git status for "
            "context." + exclusion
        )
    return (
        "There is no explicit prompt and the git diff is empty. Review the "
        "entire project by inspecting its source and configuration files, "
        "looking for correctness issues and incomplete or broken behavior."
        + exclusion
    )


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
        prompt_text = (
            f"The feature is described by this prompt: {review_basis!r}. "
            if review_basis
            else _no_prompt_review_instructions(has_diff=has_diff, docs_dir=docs_dir)
            + " "
        )
        scope_instruction = (
            "Run `git status` and `git diff` in the working directory to "
            "confirm the review scope. "
            if not review_basis
            else "Check whether the current changes satisfy each distinct "
            "requirement implied by the task. "
        )
        options = self.options(
            system_prompt=(
                "You are a skeptical QA reviewer. You did not write this code "
                "-- grade it critically. There is no Sprint Contract for this "
                "review; evaluate the current working tree instead. "
                + prompt_text
                + scope_instruction
                + "Use the working-tree context provided in the task message "
                "(git status and git diff output) as the source of truth. "
                "Mark each requirement PASS or FAIL with concrete evidence "
                "(file:line or command output). "
                + _lint_instructions(self.context.lint_commands())
                + " "
                + _verification_instructions()
                + _architecture_review_instructions(
                    check_worktree_hygiene=self.context.use_worktree
                )
                + f" Write your verdict to {review_file} with the first line "
                "starting with 'SUMMARY:' and containing a brief one- or two-"
                "sentence summary. The next line must start with 'STATUS: PASS' "
                "or 'STATUS: FAIL', followed by one line per requirement. "
                "Default to FAIL when uncertain."
            ),
            allowed_tools=["Read", "Grep", "Glob", "Bash", "Write"],
            role="reviewer",
            skills=["superpowers:verification-before-completion"],
        )
        query_prompt = (
            f"{_no_prompt_review_instructions(has_diff=has_diff, docs_dir=docs_dir)}"
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
            system_prompt=(
                "You are a skeptical QA reviewer. You did not write this "
                "code -- grade it critically. You are reviewing a GitLab "
                "merge request's diff, not a local working tree -- there is "
                "no Sprint Contract and no `git diff` to run yourself. Use "
                "only the merge request title, description, and diff given "
                "in the task message as your source of truth; Read/Grep/Glob "
                "the local project only for background context on the files "
                "the diff touches, if that helps. Do not run or reference "
                "the project's lint commands -- the local checkout may not "
                "be at the merge request's commit, so their result would "
                "not reflect this diff. Mark each distinct concern PASS or "
                "FAIL with concrete evidence (a quoted diff hunk or "
                "file:line). "
                + _verification_instructions()
                + _architecture_review_instructions(
                    check_worktree_hygiene=self.context.use_worktree
                )
                + f" Write your verdict to {review_file} with the first line "
                "starting with 'SUMMARY:' and containing a brief one- or "
                "two-sentence summary. The next line must start with "
                "'STATUS: PASS' or 'STATUS: FAIL', followed by one line per "
                "concern. Default to FAIL when uncertain."
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
        focus_instruction = f" Pay particular attention to: {focus}." if focus else ""
        options = self.options(
            system_prompt=(
                "You are a skeptical QA reviewer. You did not write this code "
                f"-- grade it critically. Read the Sprint Contract in {plan_file}. "
                "Check each criterion against the actual code and mark PASS or "
                "FAIL with concrete evidence (file:line or command output)."
                + focus_instruction
                + " "
                + _lint_instructions(self.context.lint_commands())
                + " "
                + _verification_instructions()
                + _architecture_review_instructions(
                    check_worktree_hygiene=self.context.use_worktree
                )
                + f" Write your verdict to {review_file} with the first line "
                "starting with 'SUMMARY:' and containing a brief one- or two-"
                "sentence summary. The next line must start with 'STATUS: PASS' "
                "or 'STATUS: FAIL', followed by one line per criterion. "
                "Default to FAIL when uncertain."
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
