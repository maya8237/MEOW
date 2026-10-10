"""
meow/orchestrator.py

The shared generator<->reviewer round-loop engine: explorer, planner,
generator, and reviewer as real peer agents, coordinated by plain Python
control flow. Unlike the single-project version, all project-specific
values (lint commands, models, round cap) are read from `.meow/config.toml`
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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from meow.agents.base import ProjectContext
from meow.agents.generator import Generator
from meow.agents.review_fixer import ReviewFixAgent
from meow.agents.reviewer import ReviewerAgent
from meow.agents.tester import VerificationAgent
from meow.execution.sprint import Sprint, build_sprint
from meow.infrastructure.checks import code_revision
from meow.infrastructure.logging import get_logger
from meow.infrastructure.quality import (
    QualityStoreError,
    extract_concern_candidates,
    record_concerns,
)
from meow.infrastructure.test_runner import (
    VerificationSetupError,
    VerificationStageEvidence,
    prepared_test_stage,
)
from meow.infrastructure.worktree import _resolve_working_dir
from meow.project.config import load_config
from meow.project.plan_files import reject_report_name
from meow.project.shaping import ShapeContext

logger = get_logger(__name__)


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


def _capture_quality(sprint: Sprint, verdict: str) -> None:
    journal = sprint.config.get("_run_journal")
    if journal is None:
        return
    candidates = extract_concern_candidates(verdict)
    if not candidates:
        return
    try:
        record_concerns(
            sprint.repo_dir,
            journal[1],
            candidates,
            evidence_root=sprint.active_working_dir(),
        )
    except QualityStoreError as exc:
        logger.warning("quality_concerns_unavailable", reason=str(exc))


@dataclass(frozen=True)
class ReviewTestResult:
    status: Literal["PASS", "FAIL"]
    feedback: str
    reviewer_status: str
    tester_status: str | None = None


def _tester_results(
    reviewer_status: str, tester_status: str, evidence: VerificationStageEvidence
) -> dict:
    result = {"reviewer": reviewer_status, "tester": tester_status}
    if evidence.browser:
        browser = asdict(evidence.browser[0])
        browser["flows"] = list(browser["flows"])
        browser["artifacts"] = list(browser["artifacts"])
        result["browser"] = browser
    return result


def _review_file(plan_file: Path) -> Path:
    return plan_file.with_name(plan_file.stem + "-review.md")


async def _review_plan(
    sprint: Sprint, plan_file: Path, focus: str | None = None
) -> tuple[str, str]:
    kwargs: dict = {"focus": focus} if focus else {}
    shape_context = _shape_context(sprint)
    if shape_context:
        kwargs["shape_context"] = shape_context
    return await ReviewerAgent(sprint).review_plan(plan_file, **kwargs)


async def review_then_test(  # ruff: ignore[too-many-arguments]
    sprint: Sprint,
    plan_file: Path,
    round_num: int,
    *,
    initial_verdict: tuple[str, str] | None = None,
    focus: str | None = None,
) -> ReviewTestResult:
    """Run review and deterministic tests, then exploratory testing on PASS."""
    logger.info("review_test_gate_started", round=round_num, plan_file=str(plan_file))
    if initial_verdict is None:
        review_status, verdict = await _review_plan(sprint, plan_file, focus)
    else:
        review_status, verdict = initial_verdict

    _capture_quality(sprint, verdict)
    _journal(
        sprint,
        "reviewer_finished",
        review_file=str(_review_file(plan_file)),
        results={"reviewer": review_status},
    )
    if review_status != "PASS":
        label = "Lint" if "Blocking lint failures" in verdict else "Reviewer"
        return ReviewTestResult("FAIL", f"{label} feedback:\n{verdict}", review_status)

    try:
        async with prepared_test_stage(
            sprint.active_working_dir(), sprint.config
        ) as evidence:
            tester_status, tester_verdict = await VerificationAgent(sprint).test_plan(
                plan_file, evidence
            )
    except VerificationSetupError as exc:
        report = plan_file.with_name(plan_file.stem + "-test.md")
        raise RuntimeError(
            f"Tester stage setup failed for {plan_file}: {exc}. "
            f"Tester report path: {report}"
        ) from exc
    _journal(
        sprint,
        "tester_finished",
        results=_tester_results(review_status, tester_status, evidence),
    )
    if tester_status != "PASS" or evidence.blocking_failed:
        return ReviewTestResult(
            "FAIL", f"Tester feedback:\n{tester_verdict}", review_status, tester_status
        )
    return ReviewTestResult("PASS", tester_verdict, review_status, tester_status)


class PlanNotApprovedError(RuntimeError):
    """The user declined the plan when `approve_plan` was supplied."""


def log_working_directory(working_dir: Path) -> None:
    """Log the active working directory once for each meow execution."""
    logger.info("working_directory_resolved", path=str(Path(working_dir).resolve()))


def _prepare_sprint(  # ruff: ignore[too-many-arguments] -- config root is an independent provenance boundary
    working_dir: Path,
    feature_name: str | None,
    *,
    use_worktree: bool,
    source_branch: str | None = None,
    config_dir: Path | None = None,
    fresh: bool = False,
) -> tuple[Sprint, str | None, Path]:
    """Load config, resolve the active directory, and build a Sprint.

    Shared setup for `sprint_runner.run_sprint` and `run_plan`, which
    otherwise repeat this sequence almost verbatim.
    """
    reject_report_name(feature_name)
    config = load_config(config_dir or working_dir)
    active_dir, effective_name, is_worktree = _resolve_working_dir(
        working_dir,
        use_worktree=use_worktree,
        feature_name=feature_name,
        source_branch=source_branch,
        fresh=fresh,
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


async def _review_round(  # ruff: ignore[too-many-arguments]
    sprint: Sprint,
    plan_file: Path,
    round_num: int,
    *,
    test: bool,
    focus: str | None = None,
    initial_verdict: tuple[str, str] | None = None,
) -> tuple[str, str]:
    """One review (and, with `test`, tester) gate, journaled exactly once."""
    max_rounds = sprint.config.get("max_rounds")
    logger.info("reviewer_round_started", round=round_num, max_rounds=max_rounds)
    _journal(sprint, "reviewer_started", round=round_num)
    if test:
        gate = await review_then_test(
            sprint,
            plan_file,
            round_num,
            initial_verdict=initial_verdict,
            focus=focus,
        )
        status, verdict = gate.status, gate.feedback
    else:
        status, verdict = await _review_plan(sprint, plan_file, focus)
        _capture_quality(sprint, verdict)
        _journal(
            sprint,
            "reviewer_finished",
            round=round_num,
            review_file=str(_review_file(plan_file)),
            results={"reviewer": status},
        )
    logger.info(
        "reviewer_round_finished",
        round=round_num,
        status=status,
        summary=_review_summary(verdict) or "",
    )
    return status, verdict


def _fix_instruction(verdict: str, *, test: bool) -> str:
    if test:
        return f"Fix the findings, then stop.\n{verdict}"
    return (
        "The reviewer found issues. Fix them, then stop. "
        f"Reviewer feedback:\n{verdict}"
    )


async def _generate(
    generator, sprint: Sprint, instruction: str, round_num: int
) -> None:
    logger.info(
        "generator_round_started",
        round=round_num,
        max_rounds=sprint.config.get("max_rounds"),
    )
    _journal(sprint, "generator_started", round=round_num)
    try:
        await generator.implement(instruction)
    except BaseException:
        _journal(sprint, "interrupted_mutation", round=round_num)
        raise
    _journal(sprint, "generator_finished", round=round_num)


async def _run_rounds(sprint: Sprint, plan_file: Path, *, test: bool = False) -> bool:
    """Loop generator -> reviewer. True if the sprint passed."""
    async with Generator(sprint, plan_file) as generator:
        instruction = f"Implement the tasks in {plan_file}."
        for round_num in range(1, sprint.config["max_rounds"] + 1):
            await _generate(generator, sprint, instruction, round_num)
            status, verdict = await _review_round(
                sprint, plan_file, round_num, test=test
            )
            if status == "PASS":
                return True
            instruction = _fix_instruction(verdict, test=test)
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


async def _run_review_rounds(  # ruff: ignore[too-many-arguments]
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
    running a fresh review -- for `meow review --review-file`. `focus` is
    passed to every review in the loop, so it doesn't drift out of scope.
    """
    if initial_verdict is not None and not test:
        status, verdict = initial_verdict
        _capture_quality(sprint, verdict)
    else:
        status, verdict = await _review_round(
            sprint,
            plan_file,
            1,
            test=test,
            focus=focus,
            initial_verdict=initial_verdict,
        )
    if status == "PASS":
        return True

    async with Generator(sprint, plan_file) as generator:
        instruction = _fix_instruction(verdict, test=False)
        for round_num in range(2, sprint.config["max_rounds"] + 1):
            await _generate(generator, sprint, instruction, round_num)
            status, verdict = await _review_round(
                sprint, plan_file, round_num, test=test, focus=focus
            )
            if status == "PASS":
                return True
            instruction = _fix_instruction(verdict, test=test)
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
