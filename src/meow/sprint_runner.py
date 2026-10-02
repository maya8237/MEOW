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

import hashlib
import subprocess
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from meow.agents.planner import PlannerAgent
from meow.checks import (
    completion_ready,
    config_fingerprint,
    configured_checks,
    run_final_checks,
)
from meow.lint import describe_lint_plan
from meow.logging import get_logger
from meow.orchestrator import (
    PlanNotApprovedError,
    _prepare_sprint,
    _run_review_rounds,
    _run_rounds,
)
from meow.plan_files import _latest_plan_file
from meow.run_state import RunStore
from meow.shaping import ShapeContext, load_shape_artifact

logger = get_logger(__name__)


async def run_sprint(  # ruff: ignore[too-many-arguments, too-many-statements, complex-structure, too-many-branches]
    working_dir: Path,
    feature_name: str | None,
    request: str,
    *,
    use_worktree: bool = True,
    plan_file: Path | None = None,
    source_branch: str | None = None,
    approve_plan: Callable[[Path], bool] | None = None,
    resume_at: str = "generate",
    test: bool = False,
    run_id: str | None = None,
    record_root: Path | None = None,
    source: str = "prompt",
    shape_path: Path | None = None,
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
        raise ValueError(f"resume_at must be 'generate' or 'review', got {resume_at!r}")

    store = RunStore(record_root or working_dir)
    record = (
        store.load(run_id)
        if run_id
        else store.create(
            source=source,
            request=request,
            repo=record_root or working_dir,
            worktree=working_dir,
            branch="preparing",
        )
    )
    store.transition(record.id, "preparing")
    try:
        sprint, effective_name, active_dir = _prepare_sprint(
            working_dir,
            feature_name,
            use_worktree=use_worktree,
            source_branch=source_branch,
        )
    except BaseException as exc:
        store.transition(record.id, "failed", last_failure=str(exc))
        raise
    branch_result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=active_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    branch = branch_result.stdout.strip() or "detached"
    sprint.config["_run_journal"] = (store, record.id)
    if shape_path is not None:
        artifact = load_shape_artifact(shape_path)
        if hasattr(artifact, "chosen_approach"):
            sprint.config["_shape_context"] = ShapeContext(
                str(shape_path), artifact.chosen_approach, artifact.assumptions
            )
    store.transition(record.id, "prepared", worktree=str(active_dir), branch=branch)
    test = test or bool(sprint.config.get("tester", {}).get("tests"))
    describe_lint_plan(sprint.config["lint"])

    if plan_file is None:
        if resume_at == "review":
            plan_file = _latest_plan_file(active_dir / sprint.config["docs_dir"])
        else:
            store.transition(record.id, "planning")
            logger.info(
                "planner_started",
                feature_name=effective_name,
                working_dir=str(active_dir),
            )
            try:
                plan_file = await PlannerAgent(sprint).run(
                    effective_name, request, sprint.config.get("_shape_context")
                )
            except BaseException as exc:
                store.transition(record.id, "failed", last_failure=str(exc))
                raise
            logger.info("planner_finished", plan_file=str(plan_file))
    store.transition(
        record.id,
        "planned",
        plan_file=str(plan_file),
        plan_fingerprint=(
            hashlib.sha256(plan_file.read_bytes()).hexdigest()
            if plan_file.is_file()
            else None
        ),
        config_fingerprint=(
            config_fingerprint(active_dir)
            if (active_dir / ".harness.toml").is_file()
            else None
        ),
    )

    if approve_plan is not None and not approve_plan(plan_file):
        logger.warning("plan_not_approved", plan_file=str(plan_file))
        store.transition(record.id, "failed", last_failure="Plan not approved")
        raise PlanNotApprovedError(
            f"Plan {plan_file} was not approved -- stopping before the generator runs."
        )

    run_rounds = _run_review_rounds if resume_at == "review" else _run_rounds
    try:
        passed = (
            await run_rounds(sprint, plan_file, test=True)
            if test
            else await run_rounds(sprint, plan_file)
        )
    except BaseException as exc:
        if store.load(record.id).phase != "interrupted_mutation":
            store.transition(record.id, "failed", last_failure=str(exc))
        raise
    if passed:
        if (active_dir / ".harness.toml").is_file():
            store.transition(record.id, "checking")
            try:
                results = await run_final_checks(active_dir, sprint.config)
            except BaseException as exc:
                store.transition(record.id, "failed", last_failure=str(exc))
                raise
            store.transition(
                record.id,
                "checks_finished",
                results={
                    "checks": [asdict(item) for item in results],
                },
            )
            verdicts = store.load(record.id).results
            if not completion_ready(
                results,
                configured_checks(sprint.config),
                active_dir,
                verdicts.get("reviewer", "FAIL"),
                verdicts.get("tester", "FAIL") if test else None,
                reviewer_revision=verdicts.get("reviewer_revision", ""),
            ):
                store.transition(
                    record.id,
                    "failed",
                    last_failure="Required evidence failed or became stale",
                )
                raise RuntimeError(
                    "Required checks, review, or tester evidence failed; "
                    "inspect meow status"
                )
        store.transition(record.id, "complete")
        logger.info("sprint_complete", feature_name=feature_name)
        return

    logger.error(
        "sprint_did_not_pass",
        feature_name=feature_name,
        max_rounds=sprint.config["max_rounds"],
    )
    store.transition(record.id, "exhausted", last_failure="maximum rounds exhausted")
    raise RuntimeError(
        f"Sprint{f' {feature_name!r}' if feature_name else ''} did not pass "
        f"after {sprint.config['max_rounds']} "
        "rounds -- stopping instead of looping forever. Inspect the review "
        f"file {plan_file.with_name(plan_file.stem + '-review.md')}"
        + (
            f" and tester file {plan_file.with_name(plan_file.stem + '-test.md')}"
            if test
            else "."
        )
    )


async def run_plan(  # ruff: ignore[too-many-arguments] -- reducing args would change cli.py's call site
    working_dir: Path,
    feature_name: str | None,
    request: str,
    *,
    use_worktree: bool = True,
    source_branch: str | None = None,
    shape_path: Path | None = None,
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
    if shape_path is not None:
        artifact = load_shape_artifact(shape_path)
        if hasattr(artifact, "chosen_approach"):
            sprint.config["_shape_context"] = ShapeContext(
                str(shape_path), artifact.chosen_approach, artifact.assumptions
            )
    plan_file = await PlannerAgent(sprint).run(
        effective_name, request, sprint.config.get("_shape_context")
    )
    logger.info("planner_finished", plan_file=str(plan_file))
    return plan_file
