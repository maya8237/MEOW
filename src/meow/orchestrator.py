"""
meow/orchestrator.py

The shared generator<->reviewer round-loop engine: explorer, planner,
generator, and reviewer as real peer agents, coordinated by plain Python
control flow. Unlike the single-project version, all project-specific
values (lint commands, models, round cap) are read from a `.harness.toml`
file in the target project's root, not hardcoded here -- this file is meant
to be installed once and reused across projects.

This module holds only the engine: `_prepare_sprint` (shared sprint/config
setup) and the three round-loop shapes (`_run_rounds`, `_run_review_rounds`,
`_run_prompt_fix_rounds`). The CLI-facing flows that drive the engine live
in their own modules instead -- `run_sprint`/`run_plan` in
`sprint_runner.py`, `run_review_command` (every `meow run --review` source)
in `review_cli.py`, `run_issue_solver` in `issue_solver.py`, and
`run_lint_fix` in `lint_fix.py` -- rather than folding any of them into
this engine.

A project may configure any number of lint commands. Each one declares
whether it runs per edited file, whether it can auto-fix, and whether its
failure is allowed to fail a sprint -- see `_normalize_lint_commands`.

Install (from the meow repo root):    pip install -e .
Run (from inside a project repo):        meow run "Add CSV export"
"""

from collections.abc import Awaitable, Callable
from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.explorer import make_explorer_agent
from meow.agents.generator import Generator
from meow.agents.planner import run_planner
from meow.agents.review_fixer import ReviewFixAgent
from meow.agents.reviewer import (
    ReviewerAgent,
    run_prompt_reviewer,
    run_reviewer,
)
from meow.config import load_config
from meow.logging import get_logger
from meow.plan_files import (
    _detect_review_flavor,
    _latest_plan_file,
    _latest_review_file,
)
from meow.sprint import Sprint, build_sprint
from meow.worktree import _resolve_working_dir

assert run_prompt_reviewer  # re-exported for compatibility
assert run_planner and run_reviewer  # re-exported for compatibility
assert make_explorer_agent  # re-exported for compatibility
assert _detect_review_flavor and _latest_plan_file  # re-exported for compatibility
assert _latest_review_file  # re-exported for compatibility

logger = get_logger(__name__)


class PlanNotApprovedError(RuntimeError):
    """The user declined the plan when `approve_plan` was supplied."""


def log_working_directory(working_dir: Path) -> None:
    """Log the active working directory once for each meow execution."""
    logger.info("working_directory_resolved", path=str(Path(working_dir).resolve()))


def _prepare_sprint(
    working_dir: Path,
    feature_name: str | None,
    *,
    use_worktree: bool,
    source_branch: str | None = None,
) -> tuple[Sprint, str | None, Path]:
    """Load config, resolve the active directory, and build a Sprint.

    Shared setup for `sprint_runner.run_sprint` and `run_plan`, which
    otherwise repeat this sequence almost verbatim.
    """
    config = load_config(working_dir)
    active_dir, effective_name, is_worktree = _resolve_working_dir(
        working_dir,
        use_worktree=use_worktree,
        feature_name=feature_name,
        source_branch=source_branch,
    )
    sprint = build_sprint(
        working_dir,
        config,
        active_dir if is_worktree else None,
        use_worktree=is_worktree,
    )
    return sprint, effective_name, active_dir


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def _run_rounds(sprint: Sprint, plan_file: Path) -> bool:
    """Loop generator -> reviewer. True if the sprint passed."""
    max_rounds = sprint.config["max_rounds"]

    async with Generator(sprint, plan_file) as generator:
        instruction = f"Implement the tasks in {plan_file}."
        for round_num in range(1, max_rounds + 1):
            logger.info(
                "generator_round_started", round=round_num, max_rounds=max_rounds
            )
            await generator.implement(instruction)

            logger.info(
                "reviewer_round_started", round=round_num, max_rounds=max_rounds
            )
            status, verdict = await ReviewerAgent(sprint).review_plan(plan_file)
            summary = "\n".join(
                line.strip()
                for line in verdict.splitlines()
                if line.strip() and not line.startswith("STATUS:")
            )[:400]
            logger.info(
                "reviewer_round_finished",
                round=round_num,
                status=status,
                summary=summary,
            )

            if status == "PASS":
                return True

            instruction = (
                "The reviewer found issues. Fix them, then stop. "
                f"Reviewer feedback:\n{verdict}"
            )

    return False


def _review_summary(verdict: str) -> str | None:
    return next(
        (
            line.strip()
            for line in verdict.splitlines()
            if line.strip().startswith("SUMMARY:")
        ),
        None,
    )


async def _run_review_rounds(
    sprint: Sprint,
    plan_file: Path,
    *,
    initial_verdict: tuple[str, str] | None = None,
    focus: str | None = None,
) -> bool:
    """Loop reviewer -> generator, reviewing the existing code first.

    Unlike `_run_rounds`, this doesn't assume the plan is unimplemented --
    it only spins up a generator session if the first review actually finds
    something to fix. True if the plan ends up passing.

    `initial_verdict`, when given, is used as round 1's verdict instead of
    running a fresh review -- for `meow run --review --review-file`, whose
    round 1 verdict is the existing review file it started from. `focus`, when
    given, is passed to every `review_plan` call in the loop (not just the
    first), so a requested focus doesn't drift out of scope across rounds.
    """
    max_rounds = sprint.config["max_rounds"]

    if initial_verdict is None:
        logger.info("reviewer_round_started", round=1, max_rounds=max_rounds)
        status, verdict = await ReviewerAgent(sprint).review_plan(
            plan_file, focus=focus
        )
        logger.info(
            "reviewer_round_finished",
            round=1,
            status=status,
            summary=_review_summary(verdict) or "",
        )
    else:
        status, verdict = initial_verdict

    if status == "PASS":
        return True

    async with Generator(sprint, plan_file) as generator:
        instruction = (
            "The reviewer found issues. Fix them, then stop. "
            f"Reviewer feedback:\n{verdict}"
        )
        for round_num in range(2, max_rounds + 1):
            logger.info(
                "generator_round_started", round=round_num, max_rounds=max_rounds
            )
            await generator.implement(instruction)

            logger.info(
                "reviewer_round_started", round=round_num, max_rounds=max_rounds
            )
            status, verdict = await ReviewerAgent(sprint).review_plan(
                plan_file, focus=focus
            )
            summary = "\n".join(
                line.strip()
                for line in verdict.splitlines()
                if line.strip() and not line.startswith("STATUS:")
            )[:400]
            logger.info(
                "reviewer_round_finished",
                round=round_num,
                status=status,
                summary=summary,
            )

            if status == "PASS":
                return True

            instruction = (
                "The reviewer found issues. Fix them, then stop. "
                f"Reviewer feedback:\n{verdict}"
            )

    return False


async def _run_prompt_fix_rounds(
    context: ProjectContext,
    initial_verdict: tuple[str, str],
    *,
    re_review: Callable[[], Awaitable[tuple[str, str]]],
) -> bool:
    """Like `_run_review_rounds`, but for a review with no plan file or
    Sprint Contract to hand a `GeneratorAgent` -- fixes with `ReviewFixAgent`
    (a generic "fix these review findings" session) and re-reviews with
    whatever `re_review` the caller supplies (a prompt-based re-review for
    `meow run --review --fix`'s prompt/jira sources, a branch-diff
    re-review for its `--branch` source). `initial_verdict` is always required here
    (unlike `_run_review_rounds`, this has no "run a fresh review first"
    mode -- every caller already has one).
    """
    max_rounds = context.config["max_rounds"]
    status, verdict = initial_verdict
    if status == "PASS":
        return True

    async with ReviewFixAgent(context) as fixer:
        for round_num in range(2, max_rounds + 1):
            logger.info(
                "review_fix_round_started", round=round_num, max_rounds=max_rounds
            )
            await fixer.fix(verdict)

            status, verdict = await re_review()
            logger.info(
                "review_fix_round_finished",
                round=round_num,
                status=status,
                summary=_review_summary(verdict) or "",
            )
            if status == "PASS":
                return True

    return False
