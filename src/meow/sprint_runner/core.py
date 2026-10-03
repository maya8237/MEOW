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
import json
import subprocess
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from meow.agents.planner import PlannerAgent
from meow.cancellation import RunCancelled, cancellable, check_cancel
from meow.checks import (
    completion_ready,
    config_fingerprint,
    configured_checks,
    run_final_checks,
)
from meow.delivery import deliver_verified_run
from meow.lint import describe_lint_plan
from meow.logging import get_logger
from meow.orchestrator import (
    PlanNotApprovedError,
    _prepare_sprint,
    _run_review_rounds,
    _run_rounds,
)
from meow.plan_files import _latest_plan_file
from meow.plan_state import PlanStore
from meow.preplan import gather_context, prepare_preplan
from meow.run_state import RunStore
from meow.shaping import ShapeContext, load_shape_artifact
from meow.tasks.model import load_task_graph
from meow.tasks.runner import run_parallel_plan
from meow.usage import usage_scope
from meow.worktree.setup import WorktreeSetupError, run_setup

logger = get_logger(__name__)


async def run_sprint(  # ruff: ignore[too-many-arguments, too-many-statements, too-many-locals, complex-structure, too-many-branches]
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
    unattended: bool = False,
    worktree_preexisting: bool = False,
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
    check_cancel(store, record.id)
    worktree_preexisted = bool(
        feature_name and (working_dir / ".worktrees" / feature_name).exists()
    )
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
    sprint.config["_unattended"] = unattended
    if shape_path is not None:
        artifact = load_shape_artifact(shape_path)
        if hasattr(artifact, "chosen_approach"):
            sprint.config["_shape_context"] = ShapeContext(
                str(shape_path), artifact.chosen_approach, artifact.assumptions
            )
    store.transition(
        record.id,
        "prepared",
        worktree=str(active_dir),
        branch=branch,
        delivery={"unattended": unattended or record.delivery.get("unattended", False)},
    )
    manifest = sprint.config.get("worktree_setup", {"copy": [], "commands": []})
    setup_source = record_root or working_dir
    setup_done = bool(record.results.get("setup_complete"))
    if (
        active_dir != setup_source
        and not (worktree_preexisted or worktree_preexisting)
        and not setup_done
        and any(manifest.values())
    ):
        store.transition(record.id, "setting_up")
        actions: list[dict[str, object]] = []

        def record_setup_action(action: dict[str, object]) -> None:
            actions.append(action)
            store.transition(record.id, "setting_up", results={"setup": actions})

        try:
            check_cancel(store, record.id)
            run_setup(
                setup_source,
                active_dir,
                manifest,
                record_action=record_setup_action,
                command_decision=lambda argv: sprint.config["permissions"]
                .for_role("setup")
                .decision("WorktreeSetup", {"argv": argv}),
            )
            check_cancel(store, record.id)
        except (WorktreeSetupError, RunCancelled) as exc:
            if not isinstance(exc, RunCancelled):
                store.transition(record.id, "failed", last_failure=str(exc))
            raise
        store.transition(record.id, "prepared", results={"setup_complete": True})
    test = test or bool(sprint.config.get("tester", {}).get("tests"))
    describe_lint_plan(sprint.config["lint"])
    check_cancel(store, record.id)

    if plan_file is None and resume_at == "generate":
        store.transition(record.id, "knowledge")
        check_cancel(store, record.id)
        evidence = gather_context(active_dir, request)
        check_cancel(store, record.id)
        store.transition(record.id, "shaping")
        preplan = prepare_preplan(
            active_dir, request, unattended=unattended, evidence=evidence
        )
        store.transition(
            record.id,
            "shaping_finished",
            results={"preplan": preplan.to_dict()},
        )
        check_cancel(store, record.id)
        if preplan.decision.mode == "needs_user_decision":
            store.transition(
                record.id,
                "needs_user_decision",
                last_failure="Unresolved product decision before planning",
            )
            raise RuntimeError(
                "Unresolved product decision before planning; inspect meow status"
            )
        if preplan.breadboard is not None:
            store.transition(record.id, "breadboarding")
            check_cancel(store, record.id)
            store.transition(record.id, "breadboard_finished")
        artifact = store.directory / f"{record.id}.preplan.json"
        artifact.write_text(
            json.dumps(preplan.to_dict(), indent=2) + "\n", encoding="utf-8"
        )
        sprint.config["_preplan_context"] = preplan.to_dict()
        if preplan.shape is not None and shape_path is None:
            sprint.config["_shape_context"] = ShapeContext(
                str(artifact),
                preplan.shape.chosen_approach,
                preplan.shape.assumptions,
            )

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
                with usage_scope(store, record.id):
                    plan_file = await cancellable(
                        store,
                        record.id,
                        PlannerAgent(sprint).run(
                            effective_name, request, sprint.config.get("_shape_context")
                        ),
                    )
            except RunCancelled:
                raise
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
    PlanStore(active_dir).transition(plan_file, "draft", record.id)

    if approve_plan is not None and not approve_plan(plan_file):
        logger.warning("plan_not_approved", plan_file=str(plan_file))
        store.transition(record.id, "failed", last_failure="Plan not approved")
        raise PlanNotApprovedError(
            f"Plan {plan_file} was not approved -- stopping before the generator runs."
        )

    check_cancel(store, record.id)
    PlanStore(active_dir).transition(plan_file, "in-progress", record.id)

    graph = load_task_graph(plan_file) if resume_at == "generate" else None
    parallel = graph is not None and len(graph.tasks) > 1
    if parallel:
        await cancellable(
            store,
            record.id,
            run_parallel_plan(sprint, graph, plan_file, store, record.id),
        )
    run_rounds = (
        _run_review_rounds if resume_at == "review" or parallel else _run_rounds
    )
    try:
        with usage_scope(store, record.id):
            passed = await cancellable(
                store,
                record.id,
                run_rounds(sprint, plan_file, test=True)
                if test
                else run_rounds(sprint, plan_file),
            )
    except RunCancelled:
        raise
    except BaseException as exc:
        if store.load(record.id).phase != "interrupted_mutation":
            store.transition(record.id, "failed", last_failure=str(exc))
        raise
    if passed:
        if (active_dir / ".harness.toml").is_file():
            store.transition(record.id, "checking")
            try:
                results = await cancellable(
                    store, record.id, run_final_checks(active_dir, sprint.config)
                )
            except RunCancelled:
                raise
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
        if store.load(record.id).phase == "checks_finished":
            check_cancel(store, record.id)
            delivery_requested = store.load(record.id).delivery.get("unattended", False)
            deliver_verified_run(store, record.id, unattended=delivery_requested)
        check_cancel(store, record.id)
        store.transition(record.id, "complete")
        PlanStore(active_dir).transition(plan_file, "complete", record.id)
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
    evidence = gather_context(active_dir, request)
    preplan = prepare_preplan(active_dir, request, evidence=evidence)
    if preplan.decision.mode == "needs_user_decision":
        raise RuntimeError(
            "Unresolved product decision before planning; specify the intended outcome"
        )
    sprint.config["_preplan_context"] = preplan.to_dict()
    if preplan.shape is not None and shape_path is None:
        sprint.config["_shape_context"] = ShapeContext(
            "automatic preplan",
            preplan.shape.chosen_approach,
            preplan.shape.assumptions,
        )
    plan_file = await PlannerAgent(sprint).run(
        effective_name, request, sprint.config.get("_shape_context")
    )
    logger.info("planner_finished", plan_file=str(plan_file))
    return plan_file
