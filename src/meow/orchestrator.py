"""
meow/orchestrator.py

The generic harness engine: explorer, planner, generator, and reviewer as
real peer agents, coordinated by plain Python control flow. Unlike the
single-project version, all project-specific values (lint commands, models,
round cap) are read from a `.harness.toml` file in the target project's
root, not hardcoded here -- this file is meant to be installed once and
reused across projects.

A project may configure any number of lint commands. Each one declares
whether it runs per edited file, whether it can auto-fix, and whether its
failure is allowed to fail a sprint -- see `_normalize_lint_commands`.

Install (from the meow repo root):    pip install -e .
Run (from inside a project repo):        meow run "Add CSV export"
"""

from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.explorer import make_explorer_agent
from meow.agents.generator import Generator
from meow.agents.planner import PlannerAgent, run_planner
from meow.agents.reviewer import (
    PROMPT_REVIEW_FILENAME,
    ReviewerAgent,
    run_prompt_reviewer,
    run_reviewer,
)
from meow.config import load_config
from meow.lint import describe_lint_plan
from meow.logging import get_logger
from meow.sprint import Sprint, build_sprint
from meow.worktree import _resolve_working_dir

assert run_prompt_reviewer  # re-exported for compatibility
assert run_planner and run_reviewer  # re-exported for compatibility
assert make_explorer_agent  # re-exported for compatibility

logger = get_logger(__name__)


def log_working_directory(working_dir: Path) -> None:
    """Log the active working directory once for each meow execution."""
    logger.info("working_directory_resolved", path=str(Path(working_dir).resolve()))


def _prepare_sprint(
    working_dir: Path, feature_name: str | None, *, use_worktree: bool
) -> tuple[Sprint, str | None, Path]:
    """Load config, resolve the active directory, and build a Sprint.

    Shared setup for `run_sprint` and `run_plan`, which otherwise repeat this
    sequence almost verbatim.
    """
    config = load_config(working_dir)
    active_dir, effective_name, is_worktree = _resolve_working_dir(
        working_dir,
        use_worktree=use_worktree,
        feature_name=feature_name,
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


async def _run_review_rounds(sprint: Sprint, plan_file: Path) -> bool:
    """Loop reviewer -> generator, reviewing the existing code first.

    Unlike `_run_rounds`, this doesn't assume the plan is unimplemented --
    it only spins up a generator session if the first review actually finds
    something to fix. True if the plan ends up passing.
    """
    max_rounds = sprint.config["max_rounds"]

    logger.info("reviewer_round_started", round=1, max_rounds=max_rounds)
    status, verdict = await ReviewerAgent(sprint).review_plan(plan_file)
    logger.info(
        "reviewer_round_finished",
        round=1,
        status=status,
        summary=_review_summary(verdict) or "",
    )
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


def _latest_plan_file(docs_dir: Path) -> Path:
    """The most recently modified sprint plan in docs_dir, excluding reviews.

    Excludes both `<plan>-review.md` verdicts and `cr`'s own free-standing
    `review.md` report -- neither is a Sprint Contract, so picking either up
    here would hand the reviewer a review to grade as if it were a plan.
    """
    candidates = [
        path for path in docs_dir.glob("*.md")
        if not path.name.endswith("review.md")
    ]
    if not candidates:
        raise FileNotFoundError(
            f"No plan file found in {docs_dir}. Run `meow plan "
            '"<feature>"` first, or pass --plan-file explicitly.'
        )
    return max(candidates, key=lambda path: path.stat().st_mtime)


async def run_sprint(  # ruff: ignore[too-many-arguments] -- reducing args would change cli.py's call site
    working_dir: Path,
    feature_name: str | None,
    request: str,
    *,
    use_worktree: bool = True,
    plan_file: Path | None = None,
):
    sprint, effective_name, active_dir = _prepare_sprint(
        working_dir, feature_name, use_worktree=use_worktree
    )
    describe_lint_plan(sprint.config["lint"])

    if plan_file is None:
        logger.info(
            "planner_started",
            feature_name=effective_name,
            working_dir=str(active_dir),
        )
        plan_file = await PlannerAgent(sprint).run(effective_name, request)
        logger.info("planner_finished", plan_file=str(plan_file))

    if await _run_rounds(sprint, plan_file):
        logger.info("sprint_complete", feature_name=feature_name)
        return

    logger.error(
        "sprint_did_not_pass",
        feature_name=feature_name,
        max_rounds=sprint.config["max_rounds"],
    )
    raise RuntimeError(
        f"Sprint{f' {feature_name!r}' if feature_name else ''} did not pass "
        f"after {sprint.config['max_rounds']} "
        "rounds -- stopping instead of looping forever. Inspect the review "
        "file."
    )


async def run_plan(
    working_dir: Path,
    feature_name: str | None,
    request: str,
    *,
    use_worktree: bool = True,
) -> Path:
    sprint, effective_name, active_dir = _prepare_sprint(
        working_dir, feature_name, use_worktree=use_worktree
    )

    logger.info(
        "planner_started", feature_name=effective_name, working_dir=str(active_dir)
    )
    plan_file = await PlannerAgent(sprint).run(effective_name, request)
    logger.info("planner_finished", plan_file=str(plan_file))
    return plan_file


async def run_review(
    working_dir: Path,
    plan_file: Path | None,
):
    config = load_config(working_dir)
    describe_lint_plan(config["lint"])

    active_dir = working_dir
    if plan_file is not None:
        resolved_plan_file = plan_file
    else:
        resolved_plan_file = _latest_plan_file(active_dir / config["docs_dir"])
    sprint = build_sprint(working_dir, config)

    logger.info(
        "review_started", plan_file=str(resolved_plan_file), working_dir=str(active_dir)
    )
    if await _run_review_rounds(sprint, resolved_plan_file):
        logger.info("review_complete", plan_file=str(resolved_plan_file))
        return

    logger.error(
        "review_did_not_pass",
        plan_file=str(resolved_plan_file),
        max_rounds=config["max_rounds"],
    )
    raise RuntimeError(
        f"Review of {resolved_plan_file} did not pass after "
        f"{config['max_rounds']} rounds -- stopping instead of looping "
        "forever. Inspect the review file."
    )


async def run_prompt_review(
    working_dir: Path,
    prompt: str | None,
):
    """Review the current implementation against a free-text prompt.

    Unlike `run_review`, there is no Sprint Contract task list to loop a
    generator against, so this reports PASS/FAIL rather than gating on it.
    No sprint or plan file is needed either -- a generic `ProjectContext`
    built from config and the working directory is enough for the reviewer.
    """
    config = load_config(working_dir)
    describe_lint_plan(config["lint"])
    active_dir = working_dir
    context = ProjectContext(working_dir, config)

    logger.info("prompt_review_started", working_dir=str(active_dir))
    status, _ = await ReviewerAgent(context).review_prompt(prompt)
    review_file = (
        context.active_working_dir()
        / config["docs_dir"]
        / PROMPT_REVIEW_FILENAME
    )
    logger.info("prompt_review_finished", status=status, review_file=str(review_file))
