"""
meow/roles.py

The four peer agents -- explorer, planner, generator, reviewer -- as real
Claude Agent SDK sessions. Each role takes a `Sprint` for the
project-specific values it needs (models, lint commands, project root) and
nothing else; none of them know where those values came from.
"""

import re
import shutil
import subprocess
from pathlib import Path

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    TextBlock,
    query,
)

from meow.config import LintCommand
from meow.sprint import Sprint


def make_explorer_agent(config: dict) -> AgentDefinition:
    return AgentDefinition(
        description=(
            "Read-only codebase/log/test-output exploration. Use for any "
            "research whose raw output doesn't need to be kept in full."
        ),
        prompt=(
            "You are a read-only research agent. Investigate the question "
            "you're given, then return only a concise summary with "
            "file:line references -- never dump raw file contents or full "
            "command output unless specifically asked to."
        ),
        tools=["Read", "Grep", "Glob", "Bash"],
        model=config["models"]["explorer"],
    )


async def run_planner(sprint: Sprint, feature_name: str, request: str) -> Path:
    active_dir = sprint.project_root / sprint.config["docs_dir"]
    active_dir.mkdir(parents=True, exist_ok=True)
    plan_file = active_dir / f"{feature_name}.md"

    options = ClaudeAgentOptions(
        system_prompt=(
            "You are a planning agent. Consult the explorer subagent for "
            "any codebase context you need -- don't explore directly. "
            "Produce a numbered task list with acceptance criteria per "
            "task, plus a proposed '## Sprint Contract' section with "
            f"concrete, testable pass/fail criteria. Write the result to "
            f"{plan_file}. Do not write application code."
        ),
        allowed_tools=["Read", "Grep", "Glob", "Write", "Agent"],
        agents={"explorer": sprint.explorer},
        model=sprint.model("planner"),
        cwd=str(sprint.working_root()),
    )

    async for message in query(prompt=request, options=options):
        if isinstance(message, ResultMessage) and message.subtype != "success":
            raise RuntimeError(f"Planner failed: {message.subtype}")

    return plan_file


class Generator:
    def __init__(self, sprint: Sprint, plan_file: Path):
        options = ClaudeAgentOptions(
            system_prompt=(
                f"You implement tasks from {plan_file} one at a time. "
                "Work against the agreed Sprint Contract criteria exactly "
                "-- do not expand scope. When you believe a task is "
                "complete, say so explicitly and stop; do not grade your "
                "own work."
            ),
            allowed_tools=["Read", "Edit", "Write", "Bash", "Grep", "Glob", "Agent"],
            agents={"explorer": sprint.explorer},
            hooks={
                "PostToolUse": [
                    HookMatcher(matcher="Write|Edit", hooks=[sprint.lint_hook])
                ]
            },
            model=sprint.model("generator"),
            cwd=str(sprint.working_root()),
        )
        self._client = ClaudeSDKClient(options=options)

    async def __aenter__(self):
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc):
        await self._client.__aexit__(*exc)

    async def implement(self, instruction: str) -> str:
        await self._client.query(instruction)
        text = []
        async for message in self._client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text.append(block.text)
        return "\n".join(text)


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
        "inside the active worktree root and that the main repo root remains "
        "clean; any edit at the repo root is a FAIL criterion. Use file:line "
        "evidence; do not accept 'it works' as an excuse for a monolithic "
        "design."
    )


def _git_review_context(sprint: Sprint) -> str:
    """Capture the active worktree and repo-root status for reviewer checks."""
    git = shutil.which("git")
    if not git:
        return "git is not installed or not on PATH; cannot inspect the working tree."

    root = sprint.working_root()
    status = subprocess.run(
        [git, "-C", str(root), "status", "--short", "--branch"],
        check=False,
        capture_output=True,
        text=True,
    )
    diff = subprocess.run(
        [git, "-C", str(root), "diff", "--"],
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

    if sprint.working_directory and sprint.working_directory != sprint.project_root:
        repo_status = subprocess.run(
            [git, "-C", str(sprint.project_root), "status", "--short", "--branch"],
            check=False,
            capture_output=True,
            text=True,
        )
        repo_diff = subprocess.run(
            [git, "-C", str(sprint.project_root), "diff", "--"],
            check=False,
            capture_output=True,
            text=True,
        )
        context += (
            "\n\nMain repo root status (must be clean while a worktree is active):\n"
            f"{repo_status.stdout.strip() or '(no git status output at repo root)'}\n\n"
            "Main repo root diff:\n"
            f"{repo_diff.stdout.strip() or '(no diff output at repo root)'}"
        )

    return context


async def run_prompt_reviewer(
    sprint: Sprint, feature_name: str, prompt: str | None
) -> tuple[str, str]:
    """Grade the current working tree against a free-text prompt, or the git diff."""
    active_dir = sprint.project_root / sprint.config["docs_dir"]
    active_dir.mkdir(parents=True, exist_ok=True)
    review_file = active_dir / f"{feature_name}-review.md"

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
        cwd=str(sprint.working_root()),
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
    status_match = re.search(r"^STATUS:\s*(PASS|FAIL)", verdict_text, re.MULTILINE)
    status = status_match.group(1) if status_match else "FAIL"
    return status, verdict_text


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
        cwd=str(sprint.working_root()),
    )

    async for message in query(prompt=f"Review {plan_file}", options=options):
        if isinstance(message, ResultMessage) and message.subtype != "success":
            raise RuntimeError(f"Reviewer failed: {message.subtype}")

    verdict_text = review_file.read_text()
    status_match = re.search(r"^STATUS:\s*(PASS|FAIL)", verdict_text, re.MULTILINE)
    status = status_match.group(1) if status_match else "FAIL"
    return status, verdict_text
