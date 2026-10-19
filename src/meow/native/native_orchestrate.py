"""`meow native run ...`: agent-free helpers for /meow:run --orchestrate.

The session plays the judgement roles (decomposer, triage, conflict resolver,
objective reviewer). Everything deterministic goes through the same functions
the CLI driver uses: `tick`, `finish_merge`, `apply_decision`, and so on. The
prompts and query builders are the same ones the SDK agents use. Each helper
returns one JSON-serializable dict; a failure raises (the caller prints it on
stderr and exits 1). No agent is ever constructed here.
"""

import asyncio
import json
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from meow.agents.base import Agent, ProjectContext
from meow.agents.reviewer import _verdict_status, force_fail
from meow.execution.delivery import commit_worktree
from meow.execution.orchestration import review_evidence
from meow.execution.orchestration.children import failure_evidence
from meow.execution.orchestration.final_review import objective_text
from meow.execution.orchestration.intake import (
    drafts_text,
    load_tasks_file,
    save_decomposed,
)
from meow.execution.orchestration.integration import (
    abort_merge,
    ensure_integration_worktree,
    finish_merge,
    push_integration,
    reset_mid_merge,
)
from meow.execution.orchestration.model import (
    MAX_ORCHESTRATOR_TASKS,
    TERMINAL_PHASES,
    OrchestratorRecord,
    OrchestratorTask,
)
from meow.execution.orchestration.pull_request import (
    detect_provider,
    manual_pr_link,
    origin_url,
    pull_request_title,
)
from meow.execution.orchestration.report import (
    exit_code,
    render_report,
    render_status,
    write_report,
)
from meow.execution.orchestration.runner import (
    cancel_orchestrator,
    check_resumable,
    prepare_resume,
)
from meow.execution.orchestration.store import (
    DriverBusyError,
    DriverLock,
    OrchestratorStore,
)
from meow.execution.orchestration.tick import ConflictInfo, FailureInfo, TickResult
from meow.execution.orchestration.tick import tick as run_tick
from meow.execution.orchestration.triage import (
    apply_decision,
    decision_file,
    parse_decision,
    record_failure,
)
from meow.execution.run_state import RunStore
from meow.infrastructure.background import launch_background
from meow.infrastructure.checks import run_final_checks
from meow.infrastructure.worktree import _boot_repo, _ensure_clean_tree, current_branch
from meow.native.native_models import resolve_native_model
from meow.native.native_prompt import _branch_review
from meow.project.config import load_config
from meow.project.prompts import (
    decomposer_prompt,
    decomposer_query,
    integrator_prompt,
    integrator_query,
    triage_prompt,
    triage_query,
)

# Tests replace this seam; `tick` reads it at call time.
LAUNCHER = launch_background
POLL_SECONDS = 1.0
DEFAULT_WAIT_SECONDS = 300.0
# The decomposer output is rejected at most this many times (one corrective retry).
MAX_DECOMPOSE_ATTEMPTS = 2
CHECK_OUTPUT_LIMIT = 2000


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _take_lock(store: OrchestratorStore, orch_id: str) -> DriverLock:
    """Hold the native driver lock (refreshing its heartbeat) or raise."""
    lock = DriverLock(store.dir_for(orch_id), mode="native")
    if not lock.acquire():
        holder = lock.holder() or {}
        raise DriverBusyError(
            f"orchestrator {orch_id} is driven by a live "
            f"{holder.get('mode', 'unknown')} driver; wait for it to finish, or "
            f"stop it with `meow cancel {orch_id}`"
        )
    return lock


def _raise_if_cancelled(store: OrchestratorStore, orch_id: str) -> None:
    if store.cancel_marker(orch_id).exists():
        _apply_cancel_marker(store.repo, store, orch_id)
        raise ValueError(f"orchestrator {orch_id} is cancelled")


def _require_phase(record: OrchestratorRecord, *phases: str) -> None:
    if record.phase not in phases:
        raise ValueError(
            f"orchestrator {record.id} is {record.phase}, not {' or '.join(phases)}"
        )


def _task(record: OrchestratorRecord, task_id: str) -> OrchestratorTask:
    try:
        return record.task(task_id)
    except KeyError:
        raise ValueError(f"orchestrator {record.id} has no task {task_id!r}") from None


def _mark_failed(record: OrchestratorRecord, phase: str, message: str) -> None:
    record.phase = "failed"
    record.error = {"phase": phase, "message": message}


def _context(record: OrchestratorRecord, config: dict) -> ProjectContext:
    return ProjectContext(
        Path(record.integration["worktree"]), config, use_worktree=True
    )


def _provider(repo: Path, config: dict) -> str | None:
    url = origin_url(repo)
    return detect_provider(url, config) if url else None


def _decomposer_json(record: OrchestratorRecord, config: dict, output: Path) -> dict:
    agent = Agent(_context(record, config)).definition("decomposer")
    return {
        "system_prompt": decomposer_prompt(
            output,
            max_tasks=MAX_ORCHESTRATOR_TASKS,
            task_drafts=drafts_text(record.tasks) if record.tasks else None,
            agent=agent,
        ),
        "query": decomposer_query(record.objective),
        "output_file": str(output),
    }


def _source(objective: str | None, keys: list[str]) -> str:
    if objective is not None:
        return "objective"
    return "jira" if keys else "tasks"


def _tasks_objective(tasks_file: Path, keys: list[str]) -> str:
    if keys:
        return "Resolve Jira issues " + ", ".join(keys)
    return f"Run the tasks in {tasks_file.name}"


def _load_drafts(tasks_file: Path | None) -> tuple[list[OrchestratorTask], list[str]]:
    drafts = load_tasks_file(tasks_file) if tasks_file else []
    keys = [task.source_ref for task in drafts if task.source_ref]
    if keys and len(keys) != len(drafts):
        raise ValueError(
            "a tasks file that names Jira issues must give every task a source_ref"
        )
    return drafts, keys


def _create_record(
    store: OrchestratorStore, config: dict, fields: dict
) -> OrchestratorRecord:
    settings = config["orchestrator"]
    return store.create(
        objective=fields["objective"],
        source=fields["source"],
        base_branch=fields["base"],
        mode="native",
        max_parallel=fields["max_parallel"] or settings["max_parallel"],
        options={
            "test": fields["test"],
            "pr": not fields["no_pr"] and settings["open_pr"],
        },
    )


def init(  # ruff: ignore[too-many-arguments]
    repo: Path,
    *,
    objective: str | None = None,
    tasks_file: Path | None = None,
    base: str | None = None,
    max_parallel: int | None = None,
    test: bool = False,
    no_pr: bool = False,
) -> dict:
    """Create the orchestrator, its integration worktree, and take the lock."""
    if (objective is None) == (tasks_file is None):
        raise ValueError("give exactly one of --objective or --tasks")
    repo = repo.resolve()
    base_branch = base or current_branch(repo)
    if base_branch == "detached":
        raise ValueError("HEAD is detached; pass --from BRANCH")
    _ensure_clean_tree(repo)
    _boot_repo(repo, include_gitignore=True)
    config = load_config(repo)
    drafts, keys = _load_drafts(tasks_file)
    store = OrchestratorStore(repo)
    created = _create_record(
        store,
        config,
        {
            "objective": objective or _tasks_objective(tasks_file, keys),
            "source": _source(objective, keys),
            "base": base_branch,
            "max_parallel": max_parallel,
            "test": test,
            "no_pr": no_pr,
        },
    )
    record = _start_or_fail(repo, store, config, (created.id, drafts, keys))
    output = store.dir_for(record.id) / "tasks.json"
    return {
        "id": record.id,
        "integration_worktree": record.integration["worktree"],
        "state_file": str(store.dir_for(record.id) / "state.json"),
        "tasks_file": str(output),
        "decomposer": _decomposer_json(record, config, output)
        if record.phase == "decomposing"
        else None,
    }


def _start_or_fail(
    repo: Path,
    store: OrchestratorStore,
    config: dict,
    started: tuple[str, list[OrchestratorTask], list[str]],
) -> OrchestratorRecord:
    orch_id, drafts, keys = started
    lock = _take_lock(store, orch_id)
    prefix = config["orchestrator"]["branch_prefix"]
    try:
        return store.update(
            orch_id, lambda rec: _start_native(repo, rec, prefix, (drafts, keys))
        )
    except Exception as exc:
        message = str(exc)
        store.update(orch_id, lambda rec: _mark_failed(rec, "decomposing", message))
        lock.release()
        raise


def _start_native(
    repo: Path,
    record: OrchestratorRecord,
    prefix: str,
    intake: tuple[list[OrchestratorTask], list[str]],
) -> None:
    drafts, keys = intake
    ensure_integration_worktree(repo, record, prefix)
    record.tasks = drafts
    record.intake["jira_keys"] = keys
    if drafts and not keys:
        record.phase = "running"


def _tasks_rejected(store: OrchestratorStore, orch_id: str, message: str) -> dict:
    def mutate(record: OrchestratorRecord) -> None:
        rejected = record.intake.get("rejections", 0) + 1
        record.intake["rejections"] = rejected
        if rejected >= MAX_DECOMPOSE_ATTEMPTS:
            _mark_failed(record, "decomposing", message)

    record = store.update(orch_id, mutate)
    return {
        "ok": False,
        "error": message,
        "retry_allowed": record.phase == "decomposing",
    }


def save_tasks(repo: Path, orch_id: str, file: Path) -> dict:
    """Validate and save the decomposer output the session wrote to `file`."""
    store = OrchestratorStore(repo.resolve())
    _take_lock(store, orch_id)
    _require_phase(store.load(orch_id), "decomposing")
    try:
        tasks = save_decomposed(
            store, orch_id, json.loads(file.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError) as exc:
        return _tasks_rejected(store, orch_id, str(exc))

    def accept(record: OrchestratorRecord) -> None:
        record.tasks = tasks
        record.phase = "running"

    store.update(orch_id, accept)
    result = {"ok": True, "tasks": len(tasks)}
    expected = store.dir_for(orch_id) / "tasks.json"
    if file.resolve() != expected.resolve():
        result["warning"] = f"Decomposer output was expected at {expected}, got {file}"
    return result


def _conflict_json(
    record: OrchestratorRecord, config: dict, info: ConflictInfo
) -> dict:
    agent = Agent(_context(record, config)).definition("integrator")
    worktree = Path(record.integration["worktree"])
    paths = list(info.paths)
    return {
        "task": info.task_id,
        "paths": paths,
        "prompt": {
            "system_prompt": integrator_prompt(worktree, paths, agent=agent),
            "query": integrator_query(
                paths, info.task_request, list(info.merged_requests)
            ),
        },
    }


def _failure_json(
    store: OrchestratorStore,
    record: OrchestratorRecord,
    config: dict,
    info: FailureInfo,
) -> dict:
    task = _task(record, info.task_id)
    output = decision_file(store.dir_for(record.id), task)
    agent = Agent(_context(record, config)).definition("triage")
    evidence = list(info.evidence)
    return {
        "task": task.id,
        "cause": info.cause,
        "evidence": evidence,
        "prompt": {
            "system_prompt": triage_prompt(output, agent=agent),
            "query": triage_query(
                task.request,
                info.cause,
                evidence,
                task.cycles,
                config["orchestrator"]["max_recovery_cycles"],
            ),
            "output_file": str(output),
        },
    }


def _tick_json(
    store: OrchestratorStore, config: dict, orch_id: str, result: TickResult
) -> dict:
    record = store.load(orch_id)
    return {
        "state": result.state,
        "launched": list(result.launched),
        "merged": list(result.merged),
        "running": list(result.running),
        "conflicts": [_conflict_json(record, config, c) for c in result.conflicts],
        "failures": [_failure_json(store, record, config, f) for f in result.failures],
    }


def tick(repo: Path, orch_id: str) -> dict:
    """One supervisor step: poll, merge, launch; report what needs the session."""
    repo = repo.resolve()
    store = OrchestratorStore(repo)
    lock = _take_lock(store, orch_id)
    _require_phase(store.load(orch_id), "running", "blocked")
    config = load_config(repo)
    result = asyncio.run(run_tick(repo, store, orch_id, config, launcher=LAUNCHER))
    if result.state in {"blocked", "cancelled"}:
        lock.release()
    return _tick_json(store, config, orch_id, result)


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _snapshot(repo: Path, store: OrchestratorStore, orch_id: str) -> dict:
    """Modification times of every live child's run journal and the cancel marker."""
    runs = RunStore(repo).directory
    stamps: dict[str, int | None] = {"cancel": _mtime(store.cancel_marker(orch_id))}
    for task in store.load(orch_id).tasks:
        if task.status == "running" and task.run_ids:
            stamps[task.run_ids[-1]] = _mtime(runs / f"{task.run_ids[-1]}.json")
    return stamps


def wait(repo: Path, orch_id: str, timeout: float = DEFAULT_WAIT_SECONDS) -> dict:
    """Block until a live child's journal or the cancel marker changes."""
    repo = repo.resolve()
    store = OrchestratorStore(repo)
    lock = _take_lock(store, orch_id)
    baseline = _snapshot(repo, store, orch_id)
    started = time.monotonic()
    changed = False
    while not changed and time.monotonic() - started < timeout:
        time.sleep(POLL_SECONDS)
        lock.heartbeat()
        changed = _snapshot(repo, store, orch_id) != baseline
    return {"changed": changed, "elapsed": round(time.monotonic() - started, 2)}


def _require_mid_merge(record: OrchestratorRecord, task_id: str) -> None:
    if record.integration.get("merging") != task_id:
        raise ValueError(f"task {task_id} is not mid-merge; nothing to finish or abort")


def _record_integration_failure(
    store: OrchestratorStore, orch_id: str, task_id: str, log: Path | None
) -> None:
    record = store.load(orch_id)
    evidence = failure_evidence(store.repo, record.task(task_id), integration_log=log)
    max_cycles = load_config(store.repo)["orchestrator"]["max_recovery_cycles"]
    store.update(
        orch_id,
        lambda rec: record_failure(
            rec,
            task_id,
            cause="integration",
            evidence=evidence,
            max_cycles=max_cycles,
        ),
    )


def merge_done(repo: Path, orch_id: str, task_id: str) -> dict:
    """Finish the merge the session resolved; roll back if the checks fail."""
    repo = repo.resolve()
    store = OrchestratorStore(repo)
    _take_lock(store, orch_id)
    _require_mid_merge(store.load(orch_id), task_id)
    config = load_config(repo)
    outcome = asyncio.run(finish_merge(repo, store, orch_id, task_id, config))
    if outcome.status == "checks_failed":
        _record_integration_failure(store, orch_id, task_id, outcome.log)
    return {
        "task": task_id,
        "status": outcome.status,
        "commit": outcome.commit,
        "paths": list(outcome.conflicted_paths),
    }


def _latest_integration_log(store: OrchestratorStore, orch_id: str, task_id: str):
    logs = store.dir_for(orch_id).glob(f"integration-{task_id}-*.log")
    return max(logs, key=lambda path: path.stat().st_mtime_ns, default=None)


def merge_abort(repo: Path, orch_id: str, task_id: str) -> dict:
    """Roll the merge back and send the task to triage as an integration failure."""
    repo = repo.resolve()
    store = OrchestratorStore(repo)
    _take_lock(store, orch_id)
    _require_mid_merge(store.load(orch_id), task_id)
    abort_merge(repo, store, orch_id, task_id)
    log = _latest_integration_log(store, orch_id, task_id)
    _record_integration_failure(store, orch_id, task_id, log)
    return {"task": task_id, "status": "aborted"}


def _block_invalid_decision(
    record: OrchestratorRecord, task_id: str, message: str, evidence: list[str]
) -> None:
    task = _task(record, task_id)
    task.status = "blocked"
    task.history.append({
        "at": _now(),
        "action": "blocked",
        "cause": "invalid-decision",
        "rationale": message,
        "evidence": evidence,
    })
    record.phase = "blocked"
    record.blocker = {
        "task_id": task_id,
        "cause": "invalid-decision",
        "evidence": evidence,
        "needed": f"Fix the triage decision ({message}), then resume.",
    }


def triage(repo: Path, orch_id: str, task_id: str, file: Path) -> dict:
    """Validate and apply the triage decision the session wrote to `file`."""
    repo = repo.resolve()
    store = OrchestratorStore(repo)
    _take_lock(store, orch_id)
    task = _task(store.load(orch_id), task_id)
    if task.status != "failed":
        raise ValueError(f"task {task_id} is {task.status}, not awaiting triage")
    cycles = load_config(repo)["orchestrator"]["max_recovery_cycles"]
    outcome: dict = {"action": "invalid", "pending": []}

    def mutate(record: OrchestratorRecord) -> None:
        try:
            decision = parse_decision(json.loads(file.read_text(encoding="utf-8")))
        except ValueError as exc:
            _block_invalid_decision(record, task_id, str(exc), [str(file)])
            return
        outcome["action"] = decision.action
        outcome["pending"] = apply_decision(
            repo, record, task_id, decision, max_cycles=cycles
        )

    updated = store.update(orch_id, mutate)
    status = next((t.status for t in updated.tasks if t.id == task_id), "replaced")
    return {
        "task": task_id,
        "action": outcome["action"],
        "status": status,
        "pending": outcome["pending"],
        "phase": updated.phase,
    }


def _unmerged_ids(record: OrchestratorRecord) -> list[str]:
    return [task.id for task in record.tasks if task.status != "merged"]


def _check_json(result) -> dict:
    return {
        "kind": result.kind,
        "command": result.command,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "output": result.output[-CHECK_OUTPUT_LIMIT:],
    }


def _start_round(
    store: OrchestratorStore, orch_id: str, number: int, evidence: tuple
) -> None:
    review_file, failures = evidence

    def mutate(record: OrchestratorRecord) -> None:
        record.phase = "reviewing"
        record.review.update(
            round=number,
            verdict=None,
            review_file=review_file,
            checks_passed=not failures,
            check_failures=failures,
        )

    store.update(orch_id, mutate)


def review(  # ruff: ignore[too-many-statements] -- one atomic review-round setup
    repo: Path, orch_id: str, *, host=None, available_models=None
) -> dict:
    """Run the checks and build the objective-review prompt for the next round."""
    repo = repo.resolve()
    store = OrchestratorStore(repo)
    _take_lock(store, orch_id)
    record = store.load(orch_id)
    _require_phase(record, "running", "reviewing")
    if _unmerged_ids(record):
        raise ValueError(
            "tasks are not merged yet: " + ", ".join(_unmerged_ids(record))
        )
    config = load_config(repo)
    worktree = Path(record.integration["worktree"])
    number = record.review["round"] + 1
    if record.review["round"]:
        commit_worktree(worktree, ["-m", f"Fix objective review findings ({number})"])
    results = asyncio.run(run_final_checks(worktree, config))
    _raise_if_cancelled(store, orch_id)
    evidence = review_evidence.capture(repo, worktree, config, results)
    failures = [_check_json(r) for r in results if r.required and not r.passed]
    context = _context(record, config)
    branch = record.integration["branch"]
    prompt = _branch_review(
        context, record.base_branch, branch, objective=objective_text(record)
    )
    _start_round(store, orch_id, number, (prompt["review_file"], failures))
    store.update(orch_id, lambda rec: rec.review.update(evidence))
    agent = Agent(context).definition("reviewer")
    return {
        "round": number,
        "max_rounds": config["orchestrator"]["review_rounds"],
        "checks_passed": not failures,
        "check_failures": failures,
        "system_prompt": prompt["system_prompt"],
        "query": prompt["query"],
        "review_file": prompt["review_file"],
        **resolve_native_model(
            context.model("reviewer", agent),
            host=host,
            available_models=available_models,
        ),
        "tools": list(agent.tools or ()),
    }


def _checks_appendix(failures: list[dict]) -> str:
    lines = [f"- {item['command']} (exit {item['exit_code']})" for item in failures]
    return "Required checks failed after the last change:\n" + "\n".join(lines)


def _block_objective_review(record: OrchestratorRecord, review_file: Path) -> None:
    record.phase = "blocked"
    record.blocker = {
        "task_id": None,
        "cause": "objective-review",
        "evidence": [str(review_file)],
        "needed": "Read the review findings, fix the integration branch or the "
        "affected task requests, then resume.",
    }


def _validate_review_result(repo: Path, record: OrchestratorRecord, file: Path) -> dict:
    _raise_if_cancelled(OrchestratorStore(repo), record.id)
    expected = record.review.get("review_file")
    if not expected or file.resolve() != Path(expected).resolve():
        raise ValueError("The verdict must use the expected objective review report")
    config = load_config(repo)
    if not review_evidence.current(
        repo,
        Path(record.integration["worktree"]),
        config,
        record.review,
        verify_checks=False,
    ):
        raise ValueError("Objective review evidence is stale; run review again")
    return config


def review_result(  # ruff: ignore[too-many-statements] -- validate then persist one verdict transition
    repo: Path, orch_id: str, file: Path
) -> dict:
    """Record the reviewer's verdict; PASS moves on, the last FAIL blocks."""
    repo = repo.resolve()
    store = OrchestratorStore(repo)
    _take_lock(store, orch_id)
    record = store.load(orch_id)
    _require_phase(record, "reviewing")
    config = _validate_review_result(repo, record, file)
    text = file.read_text(encoding="utf-8") if file.is_file() else ""
    failures = record.review.get("check_failures") or []
    if failures and file.is_file():
        text = force_fail(text, _checks_appendix(failures))
        file.write_text(text, encoding="utf-8")
    verdict = "FAIL" if failures else _verdict_status(text)
    if verdict == "PASS" and not review_evidence.current(
        repo, Path(record.integration["worktree"]), config, record.review
    ):
        raise ValueError(
            "Objective review required checks are missing or stale; run review again"
        )
    rounds = config["orchestrator"]["review_rounds"]

    def mutate(rec: OrchestratorRecord) -> None:
        rec.review.update(verdict=verdict, review_file=str(file))
        rec.review["report_digest"] = review_evidence.report_digest(file)
        if verdict == "PASS":
            rec.phase = "delivering"
        elif rec.review["round"] >= rounds:
            _block_objective_review(rec, file)

    updated = store.update(orch_id, mutate)
    return {
        "verdict": verdict,
        "round": updated.review["round"],
        "rounds_left": max(0, rounds - updated.review["round"]),
        "phase": updated.phase,
    }


def _skip_reason(record: OrchestratorRecord, provider: str | None) -> str | None:
    if not record.options.get("pr", True):
        return "pull request disabled (--no-pr or [orchestrator].open_pr = false)"
    if provider is None:
        return "no GitHub or GitLab remote detected for origin"
    return None


def deliver(repo: Path, orch_id: str) -> dict:
    """Push the integration branch; return what the session needs to open the PR."""
    repo = repo.resolve()
    store = OrchestratorStore(repo)
    _take_lock(store, orch_id)
    _require_phase(store.load(orch_id), "delivering")
    _validate_delivery(store, orch_id)
    try:
        push_integration(repo, store, orch_id)
    except RuntimeError as exc:
        message = str(exc)
        store.update(orch_id, lambda rec: _mark_failed(rec, "delivering", message))
        raise
    record = store.load(orch_id)
    config = load_config(repo)
    provider = _provider(repo, config)
    branch = record.integration["branch"]
    url = origin_url(repo) or ""
    skip = _skip_reason(record, provider)
    return {
        "branch": branch,
        "base": record.base_branch,
        "title": pull_request_title(record),
        "body": render_report(repo, record),
        "provider": provider,
        "manual_link": manual_pr_link(url, provider, record.base_branch, branch)
        if url
        else None,
        "open_pr": skip is None,
        "skip_reason": skip,
    }


def _validate_delivery(store: OrchestratorStore, orch_id: str) -> None:
    _raise_if_cancelled(store, orch_id)
    record = store.load(orch_id)
    try:
        review_evidence.require_delivery_review(
            store.repo, record, load_config(store.repo)
        )
    except RuntimeError:

        def invalidate(rec):
            if (
                rec.review["round"]
                >= load_config(store.repo)["orchestrator"]["review_rounds"]
            ):
                _block_objective_review(
                    rec, Path(rec.review.get("review_file") or store.dir_for(orch_id))
                )
            else:
                rec.phase = "reviewing"

        store.update(orch_id, invalidate)
        raise


def record_pr(
    repo: Path, orch_id: str, *, url: str | None = None, error: str | None = None
) -> dict:
    """Record how opening the PR went and finish the orchestrator as delivered."""
    if (url is None) == (error is None):
        raise ValueError("give exactly one of --url or --error")
    repo = repo.resolve()
    store = OrchestratorStore(repo)
    lock = _take_lock(store, orch_id)
    record = store.load(orch_id)
    if record.phase == "delivered":
        return {"phase": "delivered", "exit_code": exit_code(record)}
    _require_phase(record, "delivering")
    provider = _provider(repo, load_config(repo)) or "unknown"

    def mutate(rec: OrchestratorRecord) -> None:
        if url is not None:
            rec.delivery["pr"] = {"url": url, "provider": provider}
        else:
            rec.delivery["pr_error"] = error
        rec.phase = "delivered"

    updated = store.update(orch_id, mutate)
    report = write_report(repo, updated)
    lock.release()
    return {
        "phase": "delivered",
        "exit_code": exit_code(updated),
        "report": str(report),
    }


def status(repo: Path, orch_id: str | None = None) -> dict:
    """The orchestrator's whole journal plus the rendered rollup."""
    store = OrchestratorStore(repo.resolve())
    record = store.load(orch_id) if orch_id else store.latest()
    return {**asdict(record), "summary": render_status(record)}


def cancel(repo: Path, orch_id: str) -> dict:
    """Cancel the orchestrator and its live children."""
    repo = repo.resolve()
    store = OrchestratorStore(repo)
    record = store.load(orch_id)
    if record.phase in TERMINAL_PHASES:
        raise ValueError(f"orchestrator {orch_id} is already {record.phase}")
    record = cancel_orchestrator(repo, orch_id)
    if record.phase != "cancelled":
        _apply_cancel_marker(repo, store, orch_id)
    phase = store.load(orch_id).phase
    return {"id": orch_id, "phase": phase, "cancelled": phase == "cancelled"}


def _apply_cancel_marker(repo: Path, store: OrchestratorStore, orch_id: str) -> None:
    """Let the shared tick act on the cancel marker when no live CLI driver can."""
    lock = DriverLock(store.dir_for(orch_id), mode="native")
    if not lock.acquire():
        return
    asyncio.run(run_tick(repo, store, orch_id, load_config(repo), launcher=LAUNCHER))
    lock.release()


def release(repo: Path, orch_id: str) -> dict:
    """Drop the native driver lock so another driver can take over at once."""
    store = OrchestratorStore(repo.resolve())
    store.load(orch_id)
    lock = DriverLock(store.dir_for(orch_id), mode="native")
    if not lock.acquire():
        raise DriverBusyError(
            f"orchestrator {orch_id} is driven by a live CLI driver; "
            f"stop it with `meow cancel {orch_id}`"
        )
    lock.release()
    return {"id": orch_id, "released": True}


def _prepare_resume(
    record: OrchestratorRecord, task_id: str | None, request: str | None
) -> None:
    """The shared CLI preparation, plus the two states only native can reach."""
    prepare_resume(record, task_id, request)
    if (
        record.phase == "delivered"
        and record.delivery.get("pr_error")
        and record.options.get("pr", True)
    ):
        record.phase = "delivering"
        record.delivery["pr_error"] = None
    elif record.phase == "decomposing":
        record.intake["rejections"] = 0


def resume(
    repo: Path,
    orch_id: str,
    *,
    task_id: str | None = None,
    request: str | None = None,
) -> dict:
    """Prepare a stopped orchestrator to be driven again, without driving it."""
    repo = repo.resolve()
    store = OrchestratorStore(repo)
    _take_lock(store, orch_id)
    check_resumable(store.load(orch_id), task_id, request)
    reset = reset_mid_merge(repo, store, orch_id)
    store.update(orch_id, lambda rec: _prepare_resume(rec, task_id, request))
    updated = store.load(orch_id)
    result = {
        "id": orch_id,
        "phase": updated.phase,
        "reset_merge": reset,
        "decomposer": None,
    }
    if updated.phase == "decomposing":
        config = load_config(repo)
        prefix = config["orchestrator"]["branch_prefix"]
        updated = store.update(
            orch_id, lambda rec: ensure_integration_worktree(repo, rec, prefix)
        )
        output = store.dir_for(orch_id) / "tasks.json"
        result["decomposer"] = _decomposer_json(updated, config, output)
    return result
