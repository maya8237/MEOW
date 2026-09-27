"""Reviewer agent setup, review context, and verdict parsing."""

import re
import shutil
import subprocess
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from meow.config import LintCommand
from meow.sprint import Sprint


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


def _architecture_review_instructions() -> str:
    """Tell the reviewer to look for monolithic, SRP-breaking modules."""
    return (
        "Also perform a SOLID/SRP review. Flag any file or class that mixes "
        "multiple responsibilities, such as config parsing + agent wiring + "
        "lint hooks + orchestration + CLI handling in one module. Treat any "
        "single file that does more than one broad concern as a FAIL criterion "
        "unless the code is clearly split into cohesive helpers or classes. "
        "If the sprint used an isolated worktree, ensure every plan change stays "
        "inside the active worktree and that the main working directory remains "
        "clean; any edit there is a FAIL criterion. Use file:line "
        "evidence; do not accept 'it works' as an excuse for a monolithic "
        "design."
    )


def _git_review_context(sprint: Sprint) -> str:
    """Capture the active worktree and original working-directory status."""
    git = shutil.which("git")
    if not git:
        return "git is not installed or not on PATH; cannot inspect the working tree."

    active_dir = sprint.active_working_dir()
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
    context = (
        "Git status for the active worktree:\n"
        f"{status.stdout.strip() or '(no git status output)'}\n\n"
        "Git diff for the active worktree:\n"
        f"{diff.stdout.strip() or '(no diff output)'}"
    )

    if sprint.working_dir and sprint.working_dir != sprint.repo_dir:
        repo_status = subprocess.run(
            [git, "-C", str(sprint.repo_dir), "status", "--short", "--branch"],
            check=False,
            capture_output=True,
            text=True,
        )
        repo_diff = subprocess.run(
            [git, "-C", str(sprint.repo_dir), "diff", "--"],
            check=False,
            capture_output=True,
            text=True,
        )
        context += (
            "\n\nOriginal working-directory status (must be clean while a "
            f"worktree is active):\n{repo_status.stdout.strip() or '(no status)'}\n\n"
            "Original working-directory diff:\n"
            f"{repo_diff.stdout.strip() or '(no diff at original working directory)'}"
        )

    return context


def _verdict_status(verdict_text: str) -> str:
    status_match = re.search(r"^STATUS:\s*(PASS|FAIL)", verdict_text, re.MULTILINE)
    return status_match.group(1) if status_match else "FAIL"


async def run_prompt_reviewer(
    sprint: Sprint, prompt: str | None
) -> tuple[str, str]:
    """Grade the current working tree against a free-text prompt, or the git diff."""
    active_dir = sprint.active_working_dir() / sprint.config["docs_dir"]
    active_dir.mkdir(parents=True, exist_ok=True)
    review_file = active_dir / "review.md"

    review_basis = (prompt or "").strip()
    git_context = _git_review_context(sprint)
    prompt_text = (
        f"The feature is described by this prompt: {review_basis!r}. "
        if review_basis
        else (
            "There is no explicit prompt. Review the current working tree "
            "using the `git status` and `git diff` content below as the "
            "source of truth. "
        )
    )

    options = ClaudeAgentOptions(
        system_prompt=(
            "You are a skeptical QA reviewer. You did not write this code "
            "-- grade it critically. There is no Sprint Contract for this "
            "review; evaluate the current working tree instead. "
            + prompt_text
            + "Run `git status` and `git diff` in the working directory to "
            "see what has actually changed, then check whether the current "
            "changes satisfy each distinct requirement implied by the task. "
            "Use the following working-tree context as the source of truth: "
            + git_context
            + ". Mark each requirement PASS or FAIL with concrete evidence "
            "(file:line or command output). "
            + _lint_instructions(sprint.lint_commands())
            + " "
            + _architecture_review_instructions()
            + f" Write your verdict to {review_file} with the first line "
            "starting with 'SUMMARY:' and containing a brief one- or two-"
            "sentence summary. The next line must start with 'STATUS: PASS' "
            "or 'STATUS: FAIL', followed by one line per requirement. "
            "Default to FAIL when uncertain."
        ),
        allowed_tools=["Read", "Grep", "Glob", "Bash", "Write"],
        model=sprint.model("reviewer"),
        cwd=str(sprint.active_working_dir()),
    )

    query_prompt = (
        f"Review the working tree against the task.\n\n{git_context}"
        if not review_basis
        else f"Review the prompt: {review_basis}\n\n{git_context}"
    )
    async for message in query(prompt=query_prompt, options=options):
        if isinstance(message, ResultMessage) and message.subtype != "success":
            raise RuntimeError(f"Reviewer failed: {message.subtype}")

    verdict_text = review_file.read_text()
    return _verdict_status(verdict_text), verdict_text


async def run_reviewer(sprint: Sprint, plan_file: Path) -> tuple[str, str]:
    review_file = plan_file.with_name(plan_file.stem + "-review.md")

    options = ClaudeAgentOptions(
        system_prompt=(
            "You are a skeptical QA reviewer. You did not write this code "
            f"-- grade it critically. Read the Sprint Contract in "
            f"{plan_file}. Check each criterion against the actual code "
            "and mark PASS or FAIL with concrete evidence (file:line or "
            "command output). "
            + _lint_instructions(sprint.lint_commands())
            + " "
            + _architecture_review_instructions()
            + f" Write your verdict to {review_file} with the first line "
            "starting with 'SUMMARY:' and containing a brief one- or two-"
            "sentence summary. The next line must start with 'STATUS: PASS' "
            "or 'STATUS: FAIL', followed by one line per criterion. "
            "Default to FAIL when uncertain."
        ),
        allowed_tools=["Read", "Grep", "Glob", "Bash", "Write"],
        model=sprint.model("reviewer"),
        cwd=str(sprint.active_working_dir()),
    )

    async for message in query(prompt=f"Review {plan_file}", options=options):
        if isinstance(message, ResultMessage) and message.subtype != "success":
            raise RuntimeError(f"Reviewer failed: {message.subtype}")

    verdict_text = review_file.read_text()
    return _verdict_status(verdict_text), verdict_text
