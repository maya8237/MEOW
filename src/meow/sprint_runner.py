"""
meow/sprint_runner.py

`meow run`/`meow plan`'s top-level flows -- plan (unless a plan file is
already given) then, for `run_sprint`, implement it in a round loop. Split
out of `orchestrator.py`, which holds only the shared generator<->reviewer
round-loop engine (`_prepare_sprint`, `_run_rounds`, `_run_review_rounds`)
this module reuses, the same way `issue_solver.py`/`gitlab_reviewer.py`/
`lint_fix.py` each own their own CLI-facing flow instead of folding it into
the engine module.
"""

from collections.abc import Callable
from pathlib import Path

from meow.agents.planner import PlannerAgent
from meow.lint import describe_lint_plan
from meow.logging import get_logger
from meow.orchestrator import (
    PlanNotApprovedError,
    _prepare_sprint,
    _run_review_rounds,
    _run_rounds,
)
from meow.plan_files import _latest_plan_file

logger = get_logger(__name__)


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
      lookup `meow run --review` uses). Review the existing code first
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
