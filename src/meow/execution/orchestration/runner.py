"""The CLI driver: the orchestrator phase machine from intake to delivery.

`start_orchestrator`, `run_orchestrator` and `resume_orchestrator` each hold
the driver lock and run one `_Driver`. The driver repeats `tick` (the shared
supervisor step) and does the judgement work `tick` hands back with SDK agents:
resolving merge conflicts and triaging failed tasks. Once every task is merged
it reviews the whole objective, pushes the integration branch, and opens the
pull request. A blocked, failed, or cancelled orchestrator pushes nothing.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.orchestration import OrchestratorAgents
from meow.execution.orchestration.children import (
    Launcher,
    cancel_children,
    failure_evidence,
)
from meow.execution.orchestration.final_review import run_objective_review
from meow.execution.orchestration.intake import (
    IntakeRequest,
    decompose,
    fetch_jira_tasks,
    load_tasks_file,
)
from meow.execution.orchestration.integration import (
    abort_merge,
    ensure_integration_worktree,
    finish_merge,
    push_integration,
    reset_mid_merge,
)
from meow.execution.orchestration.model import (
    TERMINAL_PHASES,
    OrchestratorRecord,
    OrchestratorTask,
)
from meow.execution.orchestration.pull_request import open_pull_request
from meow.execution.orchestration.report import exit_code, write_report
from meow.execution.orchestration.store import DriverLock, OrchestratorStore
from meow.execution.orchestration.tick import (
    ConflictInfo,
    FailureInfo,
    block_task,
    max_recovery_cycles,
    record_conflict_resolved,
    record_task_failure,
    tick,
)
from meow.execution.orchestration.triage import apply_decision_file, decision_file
from meow.infrastructure.background import launch_background
from meow.infrastructure.logging import get_logger
from meow.infrastructure.worktree import current_branch
from meow.project.config import load_config

logger = get_logger(__name__)

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class Wiring:
    """What a driver talks to: the judgement agents, the child launcher, and
    how to wait between ticks (replaceable so tests need not really sleep)."""

    agents: OrchestratorAgents
    launcher: Launcher
    sleep: Sleep


def resolve_base(repo: Path, base_branch: str | None) -> str:
    """`--from BRANCH`, or the checked-out branch; never a detached HEAD."""
    branch = base_branch or current_branch(repo, "")
    if not branch:
        raise ValueError("HEAD is detached; check out a branch or pass --from BRANCH.")
    return branch


def objective_for(request: IntakeRequest, keys: tuple[str, ...]) -> str:
    """The objective text recorded for an intake request."""
    if request.source == "jira":
        return "Resolve Jira issues: " + ", ".join(keys)
    if request.source == "jql":
        return f"Resolve the Jira issues matching `{request.jql}`: " + ", ".join(keys)
    if request.source == "tasks":
        return f"Run the tasks in {Path(str(request.tasks_file)).name}"
    return str(request.objective)


def _history(action: str, rationale: str) -> dict:
    return {"action": action, "cause": "", "rationale": rationale, "evidence": []}


def _reset_task(record: OrchestratorRecord, target: str, request: str | None) -> None:
    try:
        task = record.task(target)
    except KeyError:
        raise ValueError(f"Unknown task {target!r}.") from None
    if task.status not in {"blocked", "failed"}:
        raise ValueError(
            f"Task {target} is {task.status}; only a blocked or failed task "
            "can be reset."
        )
    task.cycles = 0
    task.status = "pending"
    task.next_attempt = None
    note = "Resumed by the user"
    if request is not None:
        task.request = request
        note += " with a replacement request"
    task.history.append(_history("resume", note))


def _unblock(record: OrchestratorRecord, task_id: str | None, request: str | None):
    blocker = record.blocker or {}
    target = task_id or blocker.get("task_id")
    if request is not None and target is None:
        raise ValueError("--request needs a blocked task; none is blocked.")
    if target is not None:
        _reset_task(record, target, request)
    record.blocker = None
    if blocker.get("cause") == "objective-review":
        record.phase = "reviewing"
        record.review["round"] = 0
    else:
        record.phase = "running"


def check_resumable(
    record: OrchestratorRecord, task_id: str | None, request: str | None
) -> None:
    if record.phase == "cancelled":
        raise ValueError("cancelled orchestrators cannot be resumed.")
    if (task_id is not None or request is not None) and record.phase != "blocked":
        raise ValueError(
            "--task and --request apply only to a blocked orchestrator "
            f"(this one is {record.phase})."
        )


def prepare_resume(
    record: OrchestratorRecord, task_id: str | None, request: str | None
) -> None:
    """Move a blocked or failed record back into the phase machine."""
    if record.phase == "blocked":
        _unblock(record, task_id, request)
    elif record.phase == "failed":
        record.phase = (record.error or {}).get("phase", "running")
        record.error = None


class _Driver:
    def __init__(
        self,
        repo: Path,
        orch_id: str,
        config: dict,
        wiring: Wiring,
    ):
        self.repo = repo
        self.store = OrchestratorStore(repo)
        self.orch_id = orch_id
        self.config = config
        if self.store.load(orch_id).options.get("force_disable_tests"):
            from meow.infrastructure.verification_policy import disable_tests

            disable_tests(config)
        self.agents = wiring.agents
        self.launcher = wiring.launcher
        self.sleep = wiring.sleep

    def _load(self) -> OrchestratorRecord:
        return self.store.load(self.orch_id)

    def _set_phase(self, phase: str) -> None:
        self.store.update(self.orch_id, lambda r: setattr(r, "phase", phase))

    def _report(self) -> int:
        record = self._load()
        write_report(self.repo, record)
        return exit_code(record)

    def _fail(self, exc: BaseException) -> None:
        """Mark the orchestrator `failed`, remembering where it stopped."""

        def mutate(record: OrchestratorRecord) -> None:
            if record.phase in TERMINAL_PHASES:
                return
            record.error = {
                "phase": record.phase,
                "message": str(exc) or type(exc).__name__,
            }
            record.phase = "failed"

        self.store.update(self.orch_id, mutate)

    def _mark_cancelled(self) -> None:
        record = self._load()
        cancel_children(self.repo, record)
        if record.phase not in TERMINAL_PHASES:
            self._set_phase("cancelled")

    # -- the phase machine ------------------------------------------------

    async def drive(self) -> int:
        """Run from the recorded phase to a stopping phase; return the exit code."""
        try:
            await self._advance()
        except (KeyboardInterrupt, asyncio.CancelledError):
            self._mark_cancelled()
            self._report()
            raise
        except Exception as exc:
            self._fail(exc)
            self._report()
            raise
        return self._report()

    def _phase(self) -> str:
        return self._load().phase

    async def _advance(self) -> None:
        self._validate_delivery_review()
        if self._phase() in {"running", "blocked"}:
            if await self._supervise() != "all_merged":
                return
            self._set_phase("reviewing")
        if self._phase() == "reviewing":
            verdict = await run_objective_review(
                self.repo, self.store, self.orch_id, self.config, self.agents
            )
            if verdict != "PASS":
                return
            self._set_phase("delivering")
        if self._phase() == "delivering":
            await self._deliver()

    def _validate_delivery_review(self) -> None:
        from meow.execution.orchestration.review_evidence import require_delivery_review

        if self._phase() != "delivering":
            return
        record = self._load()
        try:
            require_delivery_review(self.repo, record, self.config)
        except RuntimeError:
            if record.review["round"] >= self.config["orchestrator"]["review_rounds"]:
                blocker = {
                    "task_id": None,
                    "cause": "objective-review",
                    "evidence": [record.review.get("review_file")],
                    "needed": "Review evidence is stale. Resume for a fresh review.",
                }
                self.store.update(self.orch_id, lambda r: block_task(r, blocker))
            else:
                self._set_phase("reviewing")

    async def _supervise(self) -> str:
        """Tick until every task is merged, the orchestrator is blocked, or it
        is cancelled; do the judgement work each tick hands back."""
        poll = self.config["orchestrator"]["poll_seconds"]
        while True:
            result = await tick(
                self.repo,
                self.store,
                self.orch_id,
                self.config,
                launcher=self.launcher,
            )
            if result.state in {"all_merged", "blocked", "cancelled"}:
                return result.state
            if result.state == "conflicts":
                await self._resolve(result.conflicts)
            elif result.state == "failures":
                await self._triage(result.failures)
            else:
                await self.sleep(poll)

    # -- conflicts --------------------------------------------------------

    async def _resolve(self, conflicts: tuple[ConflictInfo, ...]) -> None:
        for conflict in conflicts:
            await self._resolve_one(conflict)

    async def _resolve_one(self, conflict: ConflictInfo) -> None:
        record = self._load()
        context = ProjectContext(
            Path(record.integration["worktree"]), self.config, use_worktree=True
        )
        log = None
        try:
            await self.agents.integrator(context).resolve(
                list(conflict.paths),
                conflict.task_request,
                list(conflict.merged_requests),
            )
            outcome = await finish_merge(
                self.repo, self.store, self.orch_id, conflict.task_id, self.config
            )
            log = outcome.log
        except Exception as exc:
            logger.warning(
                "conflict_resolution_failed",
                task=conflict.task_id,
                reason=str(exc) or type(exc).__name__,
            )
        if self._load().task(conflict.task_id).status == "merged":
            record_conflict_resolved(
                self.store, self.orch_id, conflict.task_id, conflict.paths
            )
        else:
            self._integration_failed(conflict.task_id, log)

    def _integration_failed(self, task_id: str, log: Path | None) -> None:
        if self._load().integration.get("merging"):
            abort_merge(self.repo, self.store, self.orch_id, task_id)
        task = self._load().task(task_id)
        evidence = failure_evidence(self.repo, task, integration_log=log)
        failure = FailureInfo(task_id, "integration", tuple(evidence))
        record_task_failure(
            self.store, self.orch_id, failure, max_recovery_cycles(self.config)
        )

    # -- triage -----------------------------------------------------------

    async def _triage(self, failures: tuple[FailureInfo, ...]) -> None:
        for failure in failures:
            await self._triage_one(failure)
            if self._load().phase == "blocked":
                return

    async def _triage_one(self, failure: FailureInfo) -> None:
        task = self._load().task(failure.task_id)
        max_cycles = max_recovery_cycles(self.config)
        output = decision_file(self.store.dir_for(self.orch_id), task)
        try:
            await self.agents.triage(ProjectContext(self.repo, self.config)).run(
                task.request,
                failure.cause,
                list(failure.evidence),
                output,
                cycles=task.cycles,
                max_cycles=max_cycles,
            )
        except RuntimeError as exc:
            logger.warning("triage_agent_failed", task=task.id, reason=str(exc))
            blocker = {
                "task_id": task.id,
                "cause": "triage-agent-failed",
                "evidence": list(failure.evidence),
                "needed": f"Triage failed: {exc}",
            }
            self.store.update(
                self.orch_id,
                lambda r: block_task(r, blocker),
            )
            return
        self.store.update(
            self.orch_id,
            lambda r: apply_decision_file(
                self.repo, r, failure.task_id, output, max_cycles=max_cycles
            ),
        )

    # -- delivery ---------------------------------------------------------

    async def _deliver(self) -> None:
        if self.store.cancel_marker(self.orch_id).exists():
            self._mark_cancelled()
            return
        try:
            push_integration(self.repo, self.store, self.orch_id)
        except RuntimeError as exc:
            self._fail(exc)
            return
        if self._load().options.get("pr", True):
            await open_pull_request(
                self.repo, self.store, self.orch_id, self.config, self.agents
            )
        self._set_phase("delivered")
        from meow.execution.cleanup import cleanup_completed

        await asyncio.to_thread(cleanup_completed, self.repo, self.orch_id)

    # -- intake -----------------------------------------------------------

    def _worktree(self) -> Path:
        prefix = self.config["orchestrator"]["branch_prefix"]
        self.store.update(
            self.orch_id,
            lambda r: ensure_integration_worktree(self.repo, r, prefix),
        )
        return Path(self._load().integration["worktree"])

    def _describe(self, request: IntakeRequest, tasks: list[OrchestratorTask]) -> None:
        keys = tuple(t.source_ref for t in tasks if t.source_ref)

        def mutate(record: OrchestratorRecord) -> None:
            record.objective = objective_for(request, keys)
            record.intake["jira_keys"] = list(keys)

        self.store.update(self.orch_id, mutate)

    async def _plan(self, request: IntakeRequest) -> list[OrchestratorTask]:
        drafts = None
        if request.source in {"jira", "jql"}:
            drafts = await fetch_jira_tasks(
                self.repo, self.config, request, self.agents
            )
            self._describe(request, drafts)
        elif request.source == "tasks":
            loaded = load_tasks_file(Path(str(request.tasks_file)))
            self._describe(request, loaded)
            self._worktree()
            return loaded
        return await self._decompose(drafts)

    async def _decompose(
        self, drafts: list[OrchestratorTask] | None
    ) -> list[OrchestratorTask]:
        workdir = self._worktree()
        return await decompose(
            self.repo,
            self.config,
            self.store,
            self.orch_id,
            workdir=workdir,
            drafts=drafts,
            agents=self.agents,
        )

    def _store_tasks(self, tasks: list[OrchestratorTask], *, dry_run: bool) -> None:
        def mutate(record: OrchestratorRecord) -> None:
            record.tasks = tasks
            record.intake["dry_run"] = dry_run
            if not dry_run:
                record.phase = "running"

        self.store.update(self.orch_id, mutate)

    async def start(self, request: IntakeRequest, *, dry_run: bool) -> int:
        try:
            tasks = await self._plan(request)
        except Exception as exc:
            logger.error("orchestrator_intake_failed", reason=str(exc))
            self._fail(exc)
            return self._report()
        self._store_tasks(tasks, dry_run=dry_run)
        if dry_run:
            self._report()
            return 0
        return await self.drive()

    # -- resume -----------------------------------------------------------

    async def resume(self, task_id: str | None, request: str | None) -> int:
        record = self._load()
        check_resumable(record, task_id, request)
        if record.phase == "delivered":
            return await self._redeliver()
        reset_mid_merge(self.repo, self.store, self.orch_id)
        self.store.update(self.orch_id, lambda r: prepare_resume(r, task_id, request))
        if self._phase() == "decomposing" and not await self._finish_intake():
            return self._report()
        return await self.drive()

    async def _finish_intake(self) -> bool:
        """Bring a `decomposing` record to `running`; False when intake fails."""
        try:
            tasks = await self._intake_tasks(self._load())
        except Exception as exc:
            logger.error("orchestrator_intake_failed", reason=str(exc))
            self._fail(exc)
            return False
        self._store_tasks(tasks, dry_run=False)
        return True

    async def _intake_tasks(self, record: OrchestratorRecord) -> list[OrchestratorTask]:
        """The final task list of an orchestrator whose intake did not finish."""
        if record.intake.get("dry_run") and record.tasks:
            return record.tasks
        if record.source == "objective":
            return await self._decompose(None)
        if not record.tasks:
            raise ValueError(
                "Intake did not finish for this orchestrator; start a new one "
                "with the same inputs."
            )
        if record.source == "tasks":
            return record.tasks
        return await self._decompose(record.tasks)

    async def _redeliver(self) -> int:
        """A delivered orchestrator only retries a PR that failed to open."""
        if self._load().delivery.get("pr_error"):
            await open_pull_request(
                self.repo, self.store, self.orch_id, self.config, self.agents
            )
        return self._report()


def _wiring(
    agents: OrchestratorAgents | None, launcher: Launcher, sleep: Sleep
) -> Wiring:
    return Wiring(agents or OrchestratorAgents(), launcher, sleep)


async def start_orchestrator(  # ruff: ignore[too-many-arguments] -- the pinned entry point
    repo: Path,
    intake: IntakeRequest,
    *,
    base_branch: str | None = None,
    target_branch: str | None = None,
    max_parallel: int | None = None,
    test: bool = False,
    force_disable_tests: bool = False,
    open_pr: bool | None = None,
    dry_run: bool = False,
    agents: OrchestratorAgents | None = None,
    launcher: Launcher = launch_background,
    sleep: Sleep = asyncio.sleep,
) -> tuple[str, int]:
    """Create an orchestrator from `intake` and drive it; return (id, exit code).

    Raises `ValueError` before anything is created when the input is invalid or
    HEAD is detached without `base_branch`.
    """
    intake.validate()
    config = load_config(repo)
    settings = config["orchestrator"]
    base = resolve_base(repo, base_branch)
    store = OrchestratorStore(repo)
    record = store.create(
        objective=objective_for(intake, intake.jira_keys),
        source=intake.source,
        base_branch=base,
        mode="cli",
        max_parallel=max_parallel or settings["max_parallel"],
        options={
            "test": test and not force_disable_tests,
            "force_disable_tests": force_disable_tests,
            "pr": settings["open_pr"] if open_pr is None else open_pr,
            "target_branch": target_branch or config["delivery"]["target_branch"],
        },
    )
    driver = _Driver(repo, record.id, config, _wiring(agents, launcher, sleep))
    with DriverLock(store.dir_for(record.id), mode="cli"):
        return record.id, await driver.start(intake, dry_run=dry_run)


async def run_orchestrator(  # ruff: ignore[too-many-arguments] -- the pinned entry point
    repo: Path,
    orch_id: str,
    *,
    agents: OrchestratorAgents | None = None,
    launcher: Launcher = launch_background,
    sleep: Sleep = asyncio.sleep,
) -> int:
    """Drive an existing orchestrator from its recorded phase; return the exit code."""
    driver = _Driver(repo, orch_id, load_config(repo), _wiring(agents, launcher, sleep))
    with DriverLock(driver.store.dir_for(orch_id), mode="cli"):
        return await driver.drive()


async def resume_orchestrator(  # ruff: ignore[too-many-arguments] -- the pinned entry point
    repo: Path,
    orch_id: str,
    *,
    task_id: str | None = None,
    request: str | None = None,
    agents: OrchestratorAgents | None = None,
    launcher: Launcher = launch_background,
    sleep: Sleep = asyncio.sleep,
) -> int:
    """Continue an orchestrator after a stop, crash, or block; return the exit code.

    `task_id` and `request` apply only to a blocked orchestrator.
    """
    driver = _Driver(repo, orch_id, load_config(repo), _wiring(agents, launcher, sleep))
    with DriverLock(driver.store.dir_for(orch_id), mode="cli"):
        return await driver.resume(task_id, request)


def cancel_orchestrator(repo: Path, orch_id: str) -> OrchestratorRecord:
    """Ask the orchestrator to stop: drop the cancel marker, cancel live child
    runs, and mark it `cancelled` right away when no driver is running."""
    store = OrchestratorStore(repo)
    record = store.load(orch_id)
    if record.phase in TERMINAL_PHASES:
        return record
    marker = store.cancel_marker(orch_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("cancel requested\n", encoding="utf-8")
    cancel_children(repo, record)
    lock = DriverLock(store.dir_for(orch_id), mode="cli")
    if lock.acquire():
        try:
            store.update(orch_id, lambda r: setattr(r, "phase", "cancelled"))
            write_report(repo, store.load(orch_id))
        finally:
            lock.release()
    return store.load(orch_id)
