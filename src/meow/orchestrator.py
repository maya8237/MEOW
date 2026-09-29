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

from collections.abc import Callable
from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.explorer import make_explorer_agent
from meow.agents.generator import Generator
from meow.agents.planner import PlannerAgent, run_planner
from meow.agents.review_fixer import ReviewFixAgent
from meow.agents.reviewer import (
    MR_REVIEW_FILENAME,
    PROMPT_REVIEW_FILENAME,
    ReviewerAgent,
    _verdict_status,
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

    Shared setup for `run_sprint` and `run_plan`, which otherwise repeat this
    sequence almost verbatim.
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
    running a fresh review -- for `meow review-fix-review`, whose round 1
    verdict is the existing review file it started from. `focus`, when
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
    context: ProjectContext, prompt: str, initial_verdict: tuple[str, str]
) -> bool:
    """Like `_run_review_rounds`, but for a prompt-based review with no plan
    file or Sprint Contract to hand a `GeneratorAgent` -- fixes with
    `ReviewFixAgent` (a generic "fix these review findings" session, the
    prompt-based equivalent of `_run_review_rounds`' plan-scoped generator)
    and re-reviews with `ReviewerAgent.review_prompt(prompt)` instead of
    `review_plan`. `initial_verdict` is always required here (unlike
    `_run_review_rounds`, this has no "run a fresh review first" mode --
    `meow review-fix-review` is the only caller, and it always starts from
    an existing review file).
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

            status, verdict = await ReviewerAgent(context).review_prompt(prompt)
            logger.info(
                "review_fix_round_finished",
                round=round_num,
                status=status,
                summary=_review_summary(verdict) or "",
            )
            if status == "PASS":
                return True

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


def _detect_review_flavor(review_file: Path) -> str:
    """Classify a review file by its filename -- meow's own three review
    verdict formats each have a distinct, deterministic naming convention,
    so no ambiguity and no guessing is needed here."""
    name = review_file.name
    if name == MR_REVIEW_FILENAME:
        return "gitlab"
    if name == PROMPT_REVIEW_FILENAME:
        return "prompt"
    if name.endswith("-review.md"):
        return "plan"
    raise ValueError(
        f"{review_file} doesn't look like a review file meow wrote "
        "(expected a name ending in 'review.md')."
    )


def _latest_review_file(docs_dir: Path) -> Path:
    """The most recently modified review file in docs_dir, of any flavor."""
    candidates = list(docs_dir.glob("*review.md"))
    if not candidates:
        raise FileNotFoundError(
            f"No review file found in {docs_dir}. Run `meow review`, "
            "`meow cr`, or `meow gitlab-review` first, or pass "
            "--review-file explicitly."
        )
    return max(candidates, key=lambda path: path.stat().st_mtime)


async def run_review_fix_review(  # ruff: ignore[too-many-statements] -- each flavor's branch is already only a few lines; splitting further would not leave a genuinely reusable, independently-testable piece
    working_dir: Path, prompt: str, review_file: Path | None = None
) -> None:
    """Fix and re-review an existing review verdict until it passes.

    Reuses `_run_review_rounds`'s review-then-fix loop for plan-based
    review files (its GeneratorAgent genuinely has a Sprint Contract to
    work against) and `_run_prompt_fix_rounds`'s ReviewFixAgent-based loop
    for prompt-based ones (no plan file exists, so GeneratorAgent's
    hardcoded plan-file system prompt would not fit). MR-based review
    files (from `meow gitlab-review`) are rejected up front: that command
    is deliberately read-only and never checks the merge request's code
    out locally, so there is nothing on disk here to fix.
    """
    config = load_config(working_dir)
    describe_lint_plan(config["lint"])

    docs_dir = working_dir / config["docs_dir"]
    resolved_review_file = review_file or _latest_review_file(docs_dir)
    flavor = _detect_review_flavor(resolved_review_file)
    review_text = resolved_review_file.read_text(encoding="utf-8")
    initial_verdict = (_verdict_status(review_text), review_text)

    logger.info(
        "review_fix_review_started",
        review_file=str(resolved_review_file),
        flavor=flavor,
    )

    if flavor == "gitlab":
        raise RuntimeError(
            f"{resolved_review_file} is a GitLab merge request review -- "
            "`meow review-fix-review` can't fix it: there is no local "
            "checkout of the merge request's code, and `gitlab-review` "
            "never creates one. Check out the MR's branch locally and "
            "review-fix-review a plan- or prompt-based review of that "
            "checkout instead, or address the MR feedback directly."
        )

    if flavor == "plan":
        plan_file = resolved_review_file.with_name(
            resolved_review_file.name.removesuffix("-review.md") + ".md"
        )
        if not plan_file.exists():
            raise FileNotFoundError(
                f"{resolved_review_file} looks like a plan review, but its "
                f"plan file {plan_file} no longer exists."
            )
        sprint = build_sprint(working_dir, config)
        passed = await _run_review_rounds(
            sprint, plan_file, initial_verdict=initial_verdict, focus=prompt
        )
    else:
        context = ProjectContext(working_dir, config)
        passed = await _run_prompt_fix_rounds(
            context, prompt, initial_verdict=initial_verdict
        )

    if passed:
        logger.info(
            "review_fix_review_complete", review_file=str(resolved_review_file)
        )
        return

    logger.error(
        "review_fix_review_did_not_pass",
        review_file=str(resolved_review_file),
        max_rounds=config["max_rounds"],
    )
    raise RuntimeError(
        f"review-fix-review of {resolved_review_file} did not pass after "
        f"{config['max_rounds']} rounds -- stopping instead of looping "
        "forever. Inspect the review file."
    )


async def run_sprint(  # ruff: ignore[too-many-arguments] -- reducing args would change cli.py's call site
    working_dir: Path,
    feature_name: str | None,
    request: str,
    *,
    use_worktree: bool = True,
    plan_file: Path | None = None,
    source_branch: str | None = None,
    approve_plan: Callable[[Path], bool] | None = None,
    resume_at: str = "generate",
):
    """Plan (unless `plan_file` is given) then implement it in a round loop.

    `approve_plan`, when given, is called with the plan file right before
    the generator or reviewer starts using it (whether the plan was just
    written, supplied via `plan_file`, or auto-detected); a False return
    raises `PlanNotApprovedError` instead of proceeding. The actual
    prompting -- printing the plan, reading a decision -- is the caller's
    concern (see `cli._prompt_plan_approval`); this stays agnostic to how
    approval is obtained, the same way `lint_hook` stays agnostic to how a
    file gets linted.

    `resume_at` selects where the round loop picks up:
    - `"generate"` (default): unchanged behavior -- plan fresh unless
      `plan_file` is given, then run the generator first (`_run_rounds`).
    - `"review"`: skip planning; if `plan_file` wasn't given, auto-detect
      the latest plan in the active directory's `docs_dir` (the same
      lookup `meow review` uses). Review the existing code first
      (`_run_review_rounds`), and only run the generator if that review
      finds something to fix -- for continuing a sprint that was
      interrupted after the generator already produced code, without
      re-running it on code that's already there.
    """
    if resume_at not in {"generate", "review"}:
        raise ValueError(
            f"resume_at must be 'generate' or 'review', got {resume_at!r}"
        )

    sprint, effective_name, active_dir = _prepare_sprint(
        working_dir,
        feature_name,
        use_worktree=use_worktree,
        source_branch=source_branch,
    )
    describe_lint_plan(sprint.config["lint"])

    if plan_file is None:
        if resume_at == "review":
            plan_file = _latest_plan_file(active_dir / sprint.config["docs_dir"])
        else:
            logger.info(
                "planner_started",
                feature_name=effective_name,
                working_dir=str(active_dir),
            )
            plan_file = await PlannerAgent(sprint).run(effective_name, request)
            logger.info("planner_finished", plan_file=str(plan_file))

    if approve_plan is not None and not approve_plan(plan_file):
        logger.warning("plan_not_approved", plan_file=str(plan_file))
        raise PlanNotApprovedError(
            f"Plan {plan_file} was not approved -- stopping before the "
            "generator runs."
        )

    run_rounds = _run_review_rounds if resume_at == "review" else _run_rounds
    if await run_rounds(sprint, plan_file):
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


async def run_plan(  # ruff: ignore[too-many-arguments] -- reducing args would change cli.py's call site
    working_dir: Path,
    feature_name: str | None,
    request: str,
    *,
    use_worktree: bool = True,
    source_branch: str | None = None,
) -> Path:
    sprint, effective_name, active_dir = _prepare_sprint(
        working_dir,
        feature_name,
        use_worktree=use_worktree,
        source_branch=source_branch,
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
