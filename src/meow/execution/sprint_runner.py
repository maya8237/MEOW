"""`meow run`/`meow plan` flows: plan (unless a plan file is given), then
for `run_sprint` implement, verify and deliver through the round-loop engine in
`orchestrator`."""

import hashlib
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from meow.agents.planner import PlannerAgent, require_plan_file
from meow.execution.cleanup import cleanup_completed
from meow.execution.orchestrator import (
    _prepare_sprint,
)
from meow.execution.run_failures import (
    BlockingRunError,
    InvalidConfigError,
    attach_run_id,
    classify_failure,
    failure_retry_after,
)
from meow.execution.run_state import (
    PARKED_PHASE,
    TERMINAL_PHASES,
    CheckResult,
    RunStateError,
    RunStore,
)
from meow.execution.sprint import Sprint
from meow.execution.workflow.engine import LoopExhausted, RunState, run_workflow
from meow.infrastructure.cancellation import RunCancelled, check_cancel
from meow.infrastructure.checks import (
    config_fingerprint,
)
from meow.infrastructure.lint import describe_lint_plan, make_lint_hook
from meow.infrastructure.logging import configure_error_log, get_logger
from meow.infrastructure.worktree import (
    _is_linked_worktree,
    current_branch,
)
from meow.infrastructure.worktree_names import request_name
from meow.infrastructure.worktree_setup import (
    prepare_environment,
    run_setup,
)
from meow.project.config import config_paths, config_root, load_config
from meow.project.custom.definitions import discover
from meow.project.custom.diagnostics import warn_workflow
from meow.project.custom.sources import builtin_sources
from meow.project.custom.workflows import expand, fingerprint, select_workflow, walk
from meow.project.onboarding import onboard_if_needed
from meow.project.plan_files import (
    _latest_plan_file,
    planned_plan_file,
)
from meow.project.plan_state import PlanStore
from meow.project.prompts import is_bug_request

logger = get_logger(__name__)


def _owner_finished(store: RunStore, owner: str) -> bool:
    """Return True only when the owner run is verifiably settled.

    A missing or unreadable owner record counts as not finished, so a plan is
    never redrafted under a run that may still be working on it.
    """
    try:
        phase = store.load(owner).phase
    except RunStateError:
        return False
    return phase in _SETTLED_PHASES


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
    """Onboard a never-onboarded project in its config checkout.

    Returns the sprint (rebuilt when a new config was written), the directory
    later config checks must read, and the report (None when nothing was
    needed). A failure is reported, never raised.
    """
    active_dir, config_dir = dirs
    report = onboard_if_needed(active_dir, config_dir)
    if report is None:
        return sprint, config_dir, None
    checks_dir = config_dir
    if any(
        path in report["files"]
        for path in (
            ".meow/config.toml",
            ".meow/config.local.toml",
        )
    ) or report.get("detected"):
        try:
            fresh = load_config(config_dir)
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
            checks_dir = config_dir
    logger.info("onboarding_finished", **report)
    return sprint, checks_dir, report


def _run_onboarding(
    store: RunStore, run_id: str, sprint: Sprint, dirs: tuple[Path, Path]
) -> tuple[Sprint, Path]:
    sprint, checks_dir, report = _onboard_sprint(sprint, dirs)
    if report is not None:
        store.transition(run_id, "onboarded", results={"onboarding": report})
    return sprint, checks_dir


def _new_delivery(config: dict, options: dict, target: str | None) -> dict:
    if not options.get("use_worktree", True):
        return {}
    return {
        "base": target or config["delivery"]["target_branch"],
        "source_branch": options.get("source_branch"),
        "pipeline_options": options.get("pipeline_options") or {},
    }


async def run_sprint(  # ruff: ignore[too-many-arguments, too-many-statements, complex-structure, too-many-branches] -- journal setup and terminal exception ownership stay together
    working_dir: Path,
    feature_name: str | None,
    request: str,
    *,
    run_id: str | None = None,
    record_root: Path | None = None,
    source: str = "prompt",
    on_created: Callable[[str], None] | None = None,
    **options,
) -> None:
    """Journal one sprint (see `_drive_sprint` for `options`).

    Creates the run record unless `run_id` continues one, reporting a new ID
    to `on_created`. When anything
    raises before the run reached a settled phase, `_settle_failure` parks it
    resumable, or fails it for a blocking cause, so no failure path leaves a
    run looking active.
    """
    resume_at = options.get("resume_at", "generate")
    if resume_at not in {"generate", "review"}:
        raise ValueError(f"resume_at must be 'generate' or 'review', got {resume_at!r}")
    store = RunStore(Path(record_root or working_dir).resolve())
    try:
        config = load_config(Path(record_root or working_dir))
        select_workflow(config, options.get("workflow"))
    except (OSError, ValueError) as exc:
        error = (
            InvalidConfigError(str(exc))
            if isinstance(exc, ValueError)
            and not isinstance(exc, (BlockingRunError, RunStateError))
            else exc
        )
        if run_id is not None:  # a continued run must not stay "resuming"
            _settle_failure(store, run_id, error)
            attach_run_id(error, run_id)
        if error is exc:
            raise
        raise error from exc
    target = options.pop("target_branch", None)
    delivery = {
        field: options[option]
        for field, option in (("enabled", "commit"), ("push", "push"))
        if options.get(option) is not None
    }
    if run_id is None:
        delivery.update(_new_delivery(config, options, target))
        run_id = store.create(
            source=source,
            request=request,
            repo=record_root or working_dir,
            worktree=working_dir,
            branch="preparing",
            delivery=delivery,
        ).id
        if on_created is not None:
            on_created(run_id)
    elif delivery:
        record = store.load(run_id)
        store.transition(run_id, record.phase, delivery={**record.delivery, **delivery})
    try:
        configure_error_log(store.directory / f"{run_id}.error.log")
        store.set_driver(run_id)
        base = store.load(run_id).delivery.get("base", "none (not selected)")
        print(f"[run] MR target: {base} (saved for resume).", flush=True)
        await _drive_sprint(
            working_dir,
            feature_name,
            request,
            run_id=run_id,
            record_root=record_root,
            **options,
        )
    except RunCancelled:
        raise
    except BaseException as exc:
        _settle_failure(store, run_id, exc)
        attach_run_id(exc, run_id)
        raise


def _settle_failure(store: RunStore, run_id: str, exc: BaseException) -> None:
    """Settle an unsettled run: blocking causes fail it, the rest park it.

    An unreadable journal must not hide the original cause.
    """
    try:
        if store.load(run_id).phase in _SETTLED_PHASES:
            return
        if isinstance(exc, LoopExhausted):  # a verdict, not a crash
            store.transition(run_id, "exhausted", last_failure=str(exc))
            return
        kind, cause = classify_failure(exc)
        if kind == "blocking":
            store.transition(
                run_id,
                "failed",
                last_failure=str(exc) or type(exc).__name__,
                results={"failure": {"kind": kind, "cause": cause}},
            )
        else:
            store.park(run_id, exc, kind, cause, retry_after=failure_retry_after(exc))
    except (RunStateError, OSError) as journal_error:
        logger.error(
            "run_failure_not_recorded", run_id=run_id, reason=str(journal_error)
        )


# A run in one of these phases already says why it stopped.
_SETTLED_PHASES = TERMINAL_PHASES | {"interrupted_mutation", PARKED_PHASE}


async def _drive_sprint(  # ruff: ignore[too-many-arguments, too-many-statements, too-many-locals, complex-structure, too-many-branches]
    working_dir: Path,
    feature_name: str | None,
    request: str,
    *,
    run_id: str,
    use_worktree: bool = True,
    plan_file: Path | None = None,
    source_branch: str | None = None,
    approve_plan: Callable[[Path], bool] | None = None,
    resume_at: str = "generate",
    test: bool = False,
    force_disable_tests: bool = False,
    record_root: Path | None = None,
    commit: bool | None = None,
    push: bool | None = None,
    worktree_preexisting: bool = False,
    required_session_roles: set[str] | None = None,
    workflow: str | None = None,
    inputs: dict[str, str] | None = None,
    pipeline_options: dict[str, bool] | None = None,
) -> None:
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
    config_dir = Path(record_root or working_dir).resolve()
    store = RunStore(config_dir)
    # A resumed run works inside a worktree that auto-onboarding configured
    # before the repo itself had a config; keep reading it from there.
    settings_dir = config_root(config_dir, working_dir)
    record = store.load(run_id)
    force_disable_tests = force_disable_tests or bool(
        record.results.get("force_disable_tests")
    )
    test = test or bool(
        record.results.get("test_requested", record.results.get("test", False))
    )
    if force_disable_tests:
        test = False
    store.transition(
        record.id,
        "preparing",
        results={
            "test": test,
            "test_requested": test,
            "force_disable_tests": force_disable_tests,
        },
    )
    check_cancel(store, record.id)
    # A run that plans from scratch gets its own worktree rather than reusing
    # an earlier one by name; continuing (--plan, --resume-at review)
    # keeps reuse. `meow resume` passes use_worktree=False, so it is unaffected.
    fresh = bool(source_branch) or (plan_file is None and resume_at == "generate")
    if use_worktree and not feature_name:
        feature_name = request_name(request)
    if use_worktree and plan_file is not None:
        # A plan inside .worktrees/<X>/ belongs to that worktree, whatever
        # --name says (a fresh plan may have landed in `<name>-2`).
        feature_name = _worktree_name_of(working_dir, plan_file) or feature_name
    worktree_preexisted = bool(
        feature_name
        and not fresh
        and (working_dir / ".worktrees" / feature_name).exists()
    )
    sprint, effective_name, active_dir = _prepare_sprint(
        working_dir,
        feature_name,
        use_worktree=use_worktree,
        source_branch=source_branch,
        config_dir=settings_dir,
        fresh=fresh,
    )
    branch = current_branch(active_dir)
    sprint.config["_run_journal"] = (store, record.id)
    sprint.config["_bug_mode"] = is_bug_request(request)
    if required_session_roles:
        sprint.config["_resume_required_roles"] = set(required_session_roles)
    store.transition(
        record.id,
        "prepared",
        worktree=str(active_dir),
        branch=branch,
        delivery={
            **record.delivery,
            "enabled": (
                record.delivery.get("enabled", _is_linked_worktree(active_dir))
                if commit is None
                else commit
            ),
            "push": record.delivery.get("push", True) if push is None else push,
            **({"pipeline_options": pipeline_options} if pipeline_options else {}),
        },
        results={"feature_name": effective_name} if effective_name else {},
    )
    manifest = sprint.config.get("worktree_setup", {"copy": [], "commands": []})
    setup_source = record_root or working_dir
    setup_done = bool(record.results.get("setup_complete"))
    setup_needed = (active_dir / ".meow" / "worktree-setup.pending").exists() or not (
        worktree_preexisted or worktree_preexisting or setup_done
    )
    if active_dir != setup_source and setup_needed and any(manifest.values()):
        store.transition(record.id, "setting_up")
        actions: list[dict[str, object]] = []

        def record_setup_action(action: dict[str, object]) -> None:
            actions.append(action)
            store.transition(record.id, "setting_up", results={"setup": actions})

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
            timeout=sprint.config.get("install_timeout"),
        )
        check_cancel(store, record.id)
        store.transition(record.id, "prepared", results={"setup_complete": True})
    dependency_actions = prepare_environment(
        setup_source, active_dir, sprint.config, new=False
    )
    if dependency_actions:
        store.update_results(
            record.id,
            setup=[
                *store.load(record.id).results.get("setup", []),
                *dependency_actions,
            ],
        )
    sprint, checks_dir = _run_onboarding(
        store, record.id, sprint, (active_dir, settings_dir)
    )
    describe_lint_plan(sprint.config["lint"])
    check_cancel(store, record.id)

    catalog = sprint.config.get("customizations") or discover(builtin_sources())
    sprint.config["customizations"] = catalog
    saved_workflow = record.results.get("workflow")
    definition = select_workflow(sprint.config, workflow, saved_workflow)
    warn_workflow(definition, catalog)
    from meow.infrastructure.verification_policy import disable_tests

    test_requested = test
    skipped = disable_tests(sprint.config) if force_disable_tests else []
    auto_test = (
        definition.auto_test
        and bool(sprint.config.get("tester", {}).get("tests"))
        and sprint.config.get("tester", {}).get("auto_test", True)
    )
    test = not force_disable_tests and (test_requested or auto_test)
    store.update_results(
        record.id,
        test=test,
        test_requested=test_requested,
        force_disable_tests=force_disable_tests,
        skipped_checks=skipped,
    )
    if skipped:
        logger.info("tests_disabled", skipped=skipped)
    steps = expand(definition, catalog)
    plan_given = plan_file is not None
    if (
        plan_file is None
        and resume_at == "review"
        and any(step.target == "builtin.plan" for step in steps)
    ):
        plan_file = _latest_plan_file(active_dir / sprint.config["docs_dir"])

    def plan_ready(path):
        store.transition(
            record.id,
            "planned",
            plan_file=str(path),
            plan_fingerprint=(
                hashlib.sha256(path.read_bytes()).hexdigest()
                if path.is_file()
                else None
            ),
            config_fingerprint=(
                config_fingerprint(checks_dir) if config_paths(checks_dir) else None
            ),
        )
        PlanStore(active_dir).claim(
            path, record.id, owner_finished=lambda owner: _owner_finished(store, owner)
        )

    if plan_file is not None:
        plan_ready(plan_file)
    state = RunState(
        sprint=sprint,
        store=store,
        run_id=record.id,
        plan_file=plan_file,
        flags={
            "plan_given": plan_given,
            "resume_review": resume_at == "review",
            "label_feature_name": feature_name,
            "short_plan": definition.short_plan,
            "test_requested": test_requested,
            "auto_test": auto_test,
            "inputs": inputs
            if inputs is not None
            else (saved_workflow or {}).get("inputs", {}),
        },
        approve_plan=approve_plan,
        test=test,
        checks_dir=checks_dir,
        feature_name=effective_name,
        request=request,
        plan_ready=plan_ready,
        completed=set((saved_workflow or {}).get("completed", [])),
        workflow={
            **(saved_workflow or {}),
            "name": definition.name,
            "scope": definition.source.scope,
            "fingerprint": fingerprint(definition, catalog),
            "completed": (saved_workflow or {}).get("completed", []),
            "inputs": inputs
            if inputs is not None
            else (saved_workflow or {}).get("inputs", {}),
        },
        statuses=dict(record.results.get("step_statuses", {})),
    )
    state.flags["parallel"] = any(
        item["phase"] == "tasks_integrated" for item in record.transitions
    )
    for step_id, detail in record.results.get("stages", {}).items():
        state.statuses[step_id] = {"status": detail["status"]}
    for step in walk(steps):
        if (
            step.kind == "gate"
            and step.blocks == "delivery"
            and step.id in state.statuses
        ):
            state.delivery_gates[step.id] = state.statuses[step.id]["status"]
    if "checks" in record.results:
        stored = record.results["checks"]
        rebuilt = [CheckResult.from_record(item) for item in stored]
        state.checks_results = [item for item in rebuilt if item is not None]
    store.update_results(record.id, workflow=state.workflow)
    try:
        await run_workflow(state, steps)
    except LoopExhausted:
        logger.error(
            "sprint_did_not_pass",
            feature_name=feature_name,
            max_rounds=sprint.config["max_rounds"],
        )
        store.transition(
            record.id, "exhausted", last_failure="maximum rounds exhausted"
        )
        raise
    check_cancel(store, record.id)
    store.transition(record.id, "complete")
    if state.plan_file is not None:
        PlanStore(active_dir).transition(state.plan_file, "complete", record.id)
    logger.info("sprint_complete", feature_name=feature_name)
    cleanup_completed(config_dir, record.id)


async def run_plan(  # ruff: ignore[too-many-arguments] -- reducing args would change cli.py's call site
    working_dir: Path,
    feature_name: str | None,
    request: str,
    *,
    use_worktree: bool = True,
    source_branch: str | None = None,
) -> Path:
    if use_worktree and not feature_name:
        feature_name = request_name(request)
    sprint, effective_name, active_dir = _prepare_sprint(
        working_dir,
        feature_name,
        use_worktree=use_worktree,
        source_branch=source_branch,
        fresh=True,
    )
    prepare_environment(working_dir, active_dir, sprint.config, new=True)
    sprint, _, _ = _onboard_sprint(sprint, (active_dir, working_dir))

    PlanStore(active_dir).assert_available(
        planned_plan_file(active_dir / sprint.config["docs_dir"], effective_name),
        lambda owner: _owner_finished(RunStore(working_dir), owner),
    )
    logger.info(
        "planner_started", feature_name=effective_name, working_dir=str(active_dir)
    )
    sprint.config["_bug_mode"] = is_bug_request(request)
    plan_file = await PlannerAgent(sprint).run(effective_name, request)
    plan_file = require_plan_file(plan_file)
    logger.info("planner_finished", plan_file=str(plan_file))
    return plan_file
