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
`sprint_runner.py`, `run_review_command` (every `meow review` source)
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
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
from meow.agents.tester import TesterAgent
from meow.checks import code_revision
from meow.config import load_config
from meow.logging import get_logger
from meow.plan_files import (
    _detect_review_flavor,
    _latest_plan_file,
    _latest_review_file,
)
from meow.shaping import ShapeContext
from meow.sprint import Sprint, build_sprint
from meow.test_runner import TesterSetupError, prepared_test_stage
from meow.worktree import _resolve_working_dir

assert run_prompt_reviewer  # re-exported for compatibility
assert run_planner and run_reviewer  # re-exported for compatibility
assert make_explorer_agent  # re-exported for compatibility
assert _detect_review_flavor and _latest_plan_file  # re-exported for compatibility
assert _latest_review_file  # re-exported for compatibility

logger = get_logger(__name__)
_PUBLIC_LOAD_CONFIG = load_config


def _shape_context(sprint: Sprint) -> ShapeContext | None:
    value = sprint.config.get("_shape_context")
    return value if isinstance(value, ShapeContext) else None


def _journal(sprint: Sprint, phase: str, **patch: object) -> None:
    journal = sprint.config.get("_run_journal")
    if journal is not None:
        store, run_id = journal
        if phase == "reviewer_finished" and "results" in patch:
            patch["results"] = {
                **patch["results"],
                "reviewer_revision": code_revision(sprint.active_working_dir()),
            }
        store.transition(run_id, phase, **patch)


@dataclass(frozen=True)
class ReviewTestResult:
    status: Literal["PASS", "FAIL"]
    feedback: str
    reviewer_status: str
    tester_status: str | None = None


async def review_then_test(
    sprint: Sprint,
    plan_file: Path,
    round_num: int,
    *,
    initial_verdict: tuple[str, str] | None = None,
) -> ReviewTestResult:
    """Run review and deterministic tests, then exploratory testing on PASS."""
    import meow.orchestrator as public_orchestrator

    global ReviewerAgent, prepared_test_stage
    ReviewerAgent = public_orchestrator.ReviewerAgent
    prepared_test_stage = public_orchestrator.prepared_test_stage
    logger.info("review_test_gate_started", round=round_num, plan_file=str(plan_file))
    if initial_verdict is None:
        shape_context = _shape_context(sprint)
        review_status, verdict = await ReviewerAgent(sprint).review_plan(
            plan_file, **({"shape_context": shape_context} if shape_context else {})
        )
    else:
        review_status, verdict = initial_verdict
    if review_status != "PASS":
        _journal(
            sprint,
            "reviewer_finished",
            review_file=str(plan_file.with_name(plan_file.stem + "-review.md")),
            results={"reviewer": review_status},
        )
        label = "Lint" if "Blocking lint failures" in verdict else "Reviewer"
        return ReviewTestResult("FAIL", f"{label} feedback:\n{verdict}", review_status)

    try:
        async with prepared_test_stage(
            sprint.active_working_dir(), sprint.config
        ) as evidence:
            tester_status, tester_verdict = await TesterAgent(sprint).test_plan(
                plan_file, evidence
            )
    except TesterSetupError as exc:
        report = plan_file.with_name(plan_file.stem + "-test.md")
        raise RuntimeError(
            f"Tester stage setup failed for {plan_file}: {exc}. "
            f"Tester report path: {report}"
        ) from exc
    if tester_status != "PASS" or evidence.blocking_failed:
        _journal(
            sprint,
            "tester_finished",
            results={
                "reviewer": review_status,
                "tester": tester_status,
            },
        )
        return ReviewTestResult(
            "FAIL", f"Tester feedback:\n{tester_verdict}", review_status, tester_status
        )
    _journal(
        sprint,
        "tester_finished",
        results={
            "reviewer": review_status,
            "tester": tester_status,
        },
    )
    return ReviewTestResult("PASS", tester_verdict, review_status, tester_status)


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
    # Keep the package facade patchable for callers that historically patched
    # ``meow.orchestrator.load_config`` rather than this implementation module.
    import meow.orchestrator as public_orchestrator

    config_loader = (
        load_config
        if public_orchestrator.load_config is _PUBLIC_LOAD_CONFIG
        else public_orchestrator.load_config
    )
    config = config_loader(working_dir)
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


async def _run_rounds(  # ruff: ignore[too-many-statements]
    sprint: Sprint, plan_file: Path, *, test: bool = False
) -> bool:
    """Loop generator -> reviewer. True if the sprint passed."""
    # Preserve the long-standing patch surface at ``meow.orchestrator`` while
    # keeping implementation code in this module.
    import meow.orchestrator as public_orchestrator

    global Generator, run_planner, run_reviewer, review_then_test
    Generator = public_orchestrator.Generator
    run_planner = public_orchestrator.run_planner
    run_reviewer = public_orchestrator.run_reviewer
    review_then_test = public_orchestrator.review_then_test
    max_rounds = sprint.config["max_rounds"]

    async with Generator(sprint, plan_file) as generator:
        instruction = f"Implement the tasks in {plan_file}."
        for round_num in range(1, max_rounds + 1):
            logger.info(
                "generator_round_started", round=round_num, max_rounds=max_rounds
            )
            _journal(sprint, "generator_started", round=round_num)
            try:
                await generator.implement(instruction)
            except BaseException:
                _journal(sprint, "interrupted_mutation", round=round_num)
                raise
            _journal(sprint, "generator_finished", round=round_num)

            logger.info(
                "reviewer_round_started", round=round_num, max_rounds=max_rounds
            )
            _journal(sprint, "reviewer_started", round=round_num)
            if test:
                gate = await review_then_test(sprint, plan_file, round_num)
                status, verdict = gate.status, gate.feedback
                reviewer_status = gate.reviewer_status
            else:
                shape_context = _shape_context(sprint)
                status, verdict = await ReviewerAgent(sprint).review_plan(
                    plan_file,
                    **({"shape_context": shape_context} if shape_context else {}),
                )
                reviewer_status = status
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
            _journal(
                sprint,
                "reviewer_finished",
                round=round_num,
                review_file=str(plan_file.with_name(plan_file.stem + "-review.md")),
                results={"reviewer": reviewer_status},
            )

            if status == "PASS":
                return True

            instruction = (
                f"Fix the findings, then stop.\n{verdict}"
                if test
                else "The reviewer found issues. Fix them, then stop. "
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


async def _run_review_rounds(  # ruff: ignore[complex-structure, too-many-arguments, too-many-branches, too-many-statements]
    sprint: Sprint,
    plan_file: Path,
    *,
    initial_verdict: tuple[str, str] | None = None,
    focus: str | None = None,
    test: bool = False,
) -> bool:
    """Loop reviewer -> generator, reviewing the existing code first.

    Unlike `_run_rounds`, this doesn't assume the plan is unimplemented --
    it only spins up a generator session if the first review actually finds
    something to fix. True if the plan ends up passing.

    `initial_verdict`, when given, is used as round 1's verdict instead of
    running a fresh review -- for `meow review --review-file`, whose
    round 1 verdict is the existing review file it started from. `focus`, when
    given, is passed to every `review_plan` call in the loop (not just the
    first), so a requested focus doesn't drift out of scope across rounds.
    """
    max_rounds = sprint.config["max_rounds"]

    if test:
        _journal(sprint, "reviewer_started", round=1)
        gate = await review_then_test(
            sprint, plan_file, 1, initial_verdict=initial_verdict
        )
        status, verdict = gate.status, gate.feedback
        reviewer_status = gate.reviewer_status
    elif initial_verdict is None:
        logger.info("reviewer_round_started", round=1, max_rounds=max_rounds)
        _journal(sprint, "reviewer_started", round=1)
        shape_context = _shape_context(sprint)
        review_kwargs = {"focus": focus}
        if shape_context:
            review_kwargs["shape_context"] = shape_context
        status, verdict = await ReviewerAgent(sprint).review_plan(
            plan_file, **review_kwargs
        )
        reviewer_status = status
        logger.info(
            "reviewer_round_finished",
            round=1,
            status=status,
            summary=_review_summary(verdict) or "",
        )
        _journal(
            sprint,
            "reviewer_finished",
            round=1,
            review_file=str(plan_file.with_name(plan_file.stem + "-review.md")),
            results={"reviewer": reviewer_status},
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
            _journal(sprint, "generator_started", round=round_num)
            try:
                await generator.implement(instruction)
            except BaseException:
                _journal(sprint, "interrupted_mutation", round=round_num)
                raise
            _journal(sprint, "generator_finished", round=round_num)

            logger.info(
                "reviewer_round_started", round=round_num, max_rounds=max_rounds
            )
            _journal(sprint, "reviewer_started", round=round_num)
            if test:
                gate = await review_then_test(sprint, plan_file, round_num)
                status, verdict = gate.status, gate.feedback
                reviewer_status = gate.reviewer_status
            else:
                shape_context = _shape_context(sprint)
                review_kwargs = {"focus": focus}
                if shape_context:
                    review_kwargs["shape_context"] = shape_context
                status, verdict = await ReviewerAgent(sprint).review_plan(
                    plan_file, **review_kwargs
                )
                reviewer_status = status
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
            _journal(
                sprint,
                "reviewer_finished",
                round=round_num,
                review_file=str(plan_file.with_name(plan_file.stem + "-review.md")),
                results={"reviewer": reviewer_status},
            )

            if status == "PASS":
                return True

            instruction = (
                f"Fix the findings, then stop.\n{verdict}"
                if test
                else "The reviewer found issues. Fix them, then stop. "
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
    `meow review --fix`'s prompt/jira sources, a branch-diff
    re-review for its `--branch` source). `initial_verdict` is always required here
    (unlike `_run_review_rounds`, this has no "run a fresh review first"
    mode -- every caller already has one).
    """
    max_rounds = context.config.get("max_rounds", 3)
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
