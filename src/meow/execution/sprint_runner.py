"""`meow run`/`meow plan` flows: plan (unless a plan file is given), then
for `run_sprint` implement, verify and deliver through the round-loop engine in
`orchestrator`."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path

from meow.agents.planner import PlannerAgent
from meow.execution.delivery import deliver_verified_run
from meow.execution.orchestrator import (
    PlanNotApprovedError,
    _prepare_sprint,
    _run_review_rounds,
    _run_rounds,
)
from meow.execution.run_state import TERMINAL_PHASES, RunStateError, RunStore
from meow.execution.sprint import Sprint
from meow.infrastructure.cancellation import RunCancelled, cancellable, check_cancel
from meow.infrastructure.checks import (
    completion_ready,
    config_fingerprint,
    configured_checks,
    run_final_checks,
)
from meow.infrastructure.lint import describe_lint_plan, make_lint_hook
from meow.infrastructure.logging import get_logger
from meow.infrastructure.usage import usage_scope
from meow.infrastructure.worktree import current_branch
from meow.infrastructure.worktree_setup import WorktreeSetupError, run_setup
from meow.project.config import config_paths, load_config
from meow.project.onboarding import onboard_if_needed
from meow.project.plan_files import _latest_plan_file, planned_plan_file
from meow.project.plan_state import PlanOwnedError, PlanStore
from meow.project.preplan import gather_context, prepare_preplan
from meow.project.shaping import ShapeContext, is_bug_request, load_shape_artifact
from meow.tasks.model import load_task_graph
from meow.tasks.runner import run_parallel_plan

logger = get_logger(__name__)


def _owner_finished(store: RunStore, owner: str) -> bool:
    """Return True only when the owner run is verifiably finished.

    A missing or unreadable owner record counts as not finished, so a plan is
    never redrafted under a run that may still be working on it.
    """
    try:
        phase = store.load(owner).phase
    except RunStateError:
        return False
    return phase in TERMINAL_PHASES


def _worktree_name_of(working_dir: Path, plan_file: Path) -> str | None:
    """Name of the `.worktrees/<name>` directory holding ``plan_file``, if any."""
    try:
        relative = plan_file.resolve().relative_to(
            (working_dir / ".worktrees").resolve()
        )
    except ValueError:
        return None
    return relative.parts[0] if len(relative.parts) > 1 else None


def _onboard_sprint(
    sprint: Sprint, dirs: tuple[Path, Path]
) -> tuple[Sprint, Path, dict | None]:
    """Onboard a never-onboarded project inside the active directory.

    Returns the sprint (rebuilt when a new config was written), the directory
    later config checks must read, and the report (None when nothing was
    needed). A failure is reported, never raised.
    """
    active_dir, config_dir = dirs
    report = onboard_if_needed(active_dir, config_dir)
    if report is None:
        return sprint, config_dir, None
    checks_dir = config_dir
    if ".meow/config.toml" in report["files"]:
        try:
            fresh = load_config(active_dir)
        except (OSError, ValueError) as exc:
            report = {**report, "error": str(exc)}
        else:
            sprint.config.update(fresh)
            sprint = replace(
                sprint,
                lint_hook=make_lint_hook(
                    active_dir, fresh["lint"], fresh["lint_timeout"]
                ),
            )
            checks_dir = active_dir
    logger.info("onboarding_finished", **report)
    return sprint, checks_dir, report


def _run_onboarding(
    store: RunStore, run_id: str, sprint: Sprint, dirs: tuple[Path, Path]
) -> tuple[Sprint, Path]:
    sprint, checks_dir, report = _onboard_sprint(sprint, dirs)
    if report is not None:
        store.transition(run_id, "onboarded", results={"onboarding": report})
    return sprint, checks_dir


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
    required_session_roles: set[str] | None = None,
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

    config_dir = Path(record_root or working_dir).resolve()
    store = RunStore(config_dir)
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
    # A run that plans from scratch gets its own worktree rather than reusing
    # an earlier one by name; continuing (--plan, --resume-at review)
    # keeps reuse. `meow resume` passes use_worktree=False, so it is unaffected.
    fresh = plan_file is None and resume_at == "generate"
    if use_worktree and plan_file is not None:
        # A plan inside .worktrees/<X>/ belongs to that worktree, whatever
        # --name says (a fresh plan may have landed in `<name>-2`).
        feature_name = _worktree_name_of(working_dir, plan_file) or feature_name
    worktree_preexisted = bool(
        feature_name
        and not fresh
        and (working_dir / ".worktrees" / feature_name).exists()
    )
    try:
        sprint, effective_name, active_dir = _prepare_sprint(
            working_dir,
            feature_name,
            use_worktree=use_worktree,
            source_branch=source_branch,
            config_dir=config_dir,
            fresh=fresh,
        )
    except BaseException as exc:
        store.transition(record.id, "failed", last_failure=str(exc))
        raise
    branch = current_branch(active_dir)
    sprint.config["_run_journal"] = (store, record.id)
    sprint.config["_unattended"] = unattended
    sprint.config["_bug_mode"] = is_bug_request(request)
    if required_session_roles:
        sprint.config["_resume_required_roles"] = set(required_session_roles)
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
        results={"feature_name": effective_name} if effective_name else {},
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
                command_decision=lambda argv: (
                    sprint
                    .config["permissions"]
                    .for_role("setup")
                    .decision("WorktreeSetup", {"argv": argv})
                ),
            )
            check_cancel(store, record.id)
        except (WorktreeSetupError, RunCancelled) as exc:
            if not isinstance(exc, RunCancelled):
                store.transition(record.id, "failed", last_failure=str(exc))
            raise
        store.transition(record.id, "prepared", results={"setup_complete": True})
    sprint, checks_dir = _run_onboarding(
        store, record.id, sprint, (active_dir, config_dir)
    )
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
            # Claim the plan path before the planner writes it, so a second
            # run (same --name in place, --jira, queue) cannot clobber a plan
            # an active run is using.
            try:
                PlanStore(active_dir).claim(
                    planned_plan_file(
                        active_dir / sprint.config["docs_dir"], effective_name
                    ),
                    record.id,
                    owner_finished=lambda owner: _owner_finished(store, owner),
                )
            except PlanOwnedError as exc:
                store.transition(record.id, "failed", last_failure=str(exc))
                raise
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
            config_fingerprint(checks_dir) if config_paths(checks_dir) else None
        ),
    )
    PlanStore(active_dir).claim(
        plan_file,
        record.id,
        owner_finished=lambda owner: _owner_finished(store, owner),
    )

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
        if config_paths(checks_dir):
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
        fresh=True,
    )
    sprint, _, _ = _onboard_sprint(sprint, (active_dir, working_dir))

    PlanStore(active_dir).assert_available(
        planned_plan_file(active_dir / sprint.config["docs_dir"], effective_name),
        lambda owner: _owner_finished(RunStore(working_dir), owner),
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
    sprint.config["_bug_mode"] = is_bug_request(request)
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
