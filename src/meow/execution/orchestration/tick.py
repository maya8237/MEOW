"""The shared supervisor step.

`tick` is the one place that decides what happens next to an orchestrator:
it polls child runs, finalizes passed tasks, merges them into the integration
branch one at a time, and launches ready tasks. The CLI driver
(`runner.py`) and the native helpers (`native/native_orchestrate.py`) both
call it; only the judgement work it hands back (conflicts, failures) differs
by mode. The helpers below the supervisor are the small journal mutations the
two drivers share.
"""

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from meow.execution.orchestration.children import (
    Launcher,
    cancel_children,
    failure_evidence,
    finalize_passed,
    journal_resume,
    launch_attempt,
    poll_task,
    resume_attempt,
    resume_refusal,
)
from meow.execution.orchestration.integration import begin_merge, finish_merge
from meow.execution.orchestration.model import OrchestratorRecord
from meow.execution.orchestration.scheduler import tasks_to_launch
from meow.execution.orchestration.store import OrchestratorStore
from meow.execution.orchestration.triage import FAILURE_CAUSES, record_failure
from meow.execution.run_state import PARKED_PHASE, RunStore
from meow.infrastructure.background import launch_background
from meow.project.config import run_config

TICK_PHASES = frozenset({"running", "blocked", "cancelled"})
GIT_MERGE_FAILED = "git merge failed"


@dataclass(frozen=True)
class ConflictInfo:
    task_id: str
    paths: tuple[str, ...]
    task_request: str
    merged_requests: tuple[str, ...]


@dataclass(frozen=True)
class FailureInfo:
    task_id: str
    cause: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class TickResult:
    state: Literal[
        "waiting", "conflicts", "failures", "all_merged", "blocked", "cancelled"
    ]
    launched: tuple[str, ...] = ()
    merged: tuple[str, ...] = ()
    running: tuple[str, ...] = ()
    conflicts: tuple[ConflictInfo, ...] = ()
    failures: tuple[FailureInfo, ...] = ()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def max_recovery_cycles(config: dict) -> int:
    return config["orchestrator"]["max_recovery_cycles"]


def run_phase(repo: Path, run_id: str) -> str:
    """The workflow phase of one child run."""
    return RunStore(repo).load(run_id).phase


def conflicted_paths(worktree: Path) -> tuple[str, ...]:
    """Paths git still lists as unmerged in the integration worktree."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=U"],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    )
    return tuple(line for line in result.stdout.splitlines() if line)


def conflict_info(
    record: OrchestratorRecord, task_id: str, paths: tuple[str, ...]
) -> ConflictInfo:
    merged = tuple(record.task(done).request for done in record.integration["merged"])
    return ConflictInfo(task_id, tuple(paths), record.task(task_id).request, merged)


def last_failure(task) -> dict:
    """The newest history entry that records a failed attempt."""
    return next(
        (e for e in reversed(task.history) if e.get("cause") in FAILURE_CAUSES), {}
    )


def record_task_failure(
    store: OrchestratorStore, orch_id: str, failure: FailureInfo, max_cycles: int
) -> str:
    """Mark the task `failed`, journal why, and return `record_failure`'s verdict
    (`triage`, `blocked` or `recorded`)."""
    outcome = ""

    def mutate(record: OrchestratorRecord) -> None:
        nonlocal outcome
        record.task(failure.task_id).status = "failed"
        outcome = record_failure(
            record,
            failure.task_id,
            cause=failure.cause,
            evidence=list(failure.evidence),
            max_cycles=max_cycles,
        )

    store.update(orch_id, mutate)
    return outcome


def block_task(record: OrchestratorRecord, blocker: dict) -> None:
    """Block the orchestrator (and the named task) with `blocker`, which is
    `{task_id, cause, evidence, needed}`; `task_id` may be None."""
    task_id = blocker["task_id"]
    if task_id is not None:
        task = record.task(task_id)
        task.status = "blocked"
        task.history.append({
            "at": _now(),
            "action": "blocked",
            "cause": blocker["cause"],
            "rationale": blocker["needed"],
            "evidence": list(blocker["evidence"]),
        })
    record.phase = "blocked"
    record.blocker = blocker


def record_conflict_resolved(
    store: OrchestratorStore, orch_id: str, task_id: str, paths: tuple[str, ...]
) -> None:
    """Journal a resolved merge conflict for the report."""

    def mutate(record: OrchestratorRecord) -> None:
        record.task(task_id).history.append({
            "at": _now(),
            "action": "conflict-resolved",
            "cause": "",
            "rationale": "Resolved conflicts in " + ", ".join(paths),
            "evidence": [],
        })

    store.update(orch_id, mutate)


class _Supervisor:
    def __init__(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- one value per collaborator
        self,
        repo: Path,
        store: OrchestratorStore,
        orch_id: str,
        config: dict,
        launcher: Launcher,
    ):
        self.repo = repo
        self.store = store
        self.orch_id = orch_id
        self.config = config
        self.launcher = launcher
        self.max_cycles = max_recovery_cycles(config)
        self.launched: list[str] = []
        self.merged: list[str] = []

    def _load(self) -> OrchestratorRecord:
        return self.store.load(self.orch_id)

    def _result(self, state: str, **extra) -> TickResult:
        running = tuple(t.id for t in self._load().tasks if t.status == "running")
        return TickResult(
            state,
            launched=tuple(self.launched),
            merged=tuple(self.merged),
            running=running,
            **extra,
        )

    async def run(self) -> TickResult:
        record = self._load()
        if record.phase not in TICK_PHASES:
            raise ValueError(
                f"Orchestrator {self.orch_id} is {record.phase}; tick runs only "
                "while it is running or blocked."
            )
        if (
            record.phase == "cancelled"
            or self.store.cancel_marker(self.orch_id).exists()
        ):
            return self._cancel(record)
        self._poll_running()
        conflict = self._pending_conflict() or await self._merge_passed()
        if conflict is not None:
            return self._result("conflicts", conflicts=(conflict,))
        return self._settle()

    def _cancel(self, record: OrchestratorRecord) -> TickResult:
        cancel_children(self.repo, record)
        self.store.update(self.orch_id, lambda r: setattr(r, "phase", "cancelled"))
        return self._result("cancelled")

    def _poll_running(self) -> None:
        for task in self._load().tasks:
            if task.status != "running":
                continue
            state = poll_task(self.repo, task)
            if state == "passed":
                finalize_passed(self.repo, task)
                self.store.update(
                    self.orch_id,
                    lambda r, i=task.id: setattr(r.task(i), "status", "passed"),
                )
            elif state == "failed":
                self._fail_child(task)

    def _fail_child(self, task) -> None:
        phase = run_phase(self.repo, task.run_ids[-1])
        if phase == PARKED_PHASE and self._resumed(task):
            return
        interrupted = phase in {"interrupted_mutation", PARKED_PHASE}
        failure = FailureInfo(
            task.id,
            "worker-died" if interrupted else "child-failed",
            tuple(failure_evidence(self.repo, task)),
        )
        record_task_failure(self.store, self.orch_id, failure, self.max_cycles)

    def _resumed(self, task) -> bool:
        """Resume a parked child within budget before it reaches triage;
        otherwise journal why not and let triage decide."""
        run = self.config.get("run") or run_config(self.repo)
        reason = resume_refusal(self.repo, task, run)
        if reason is None:
            return bool(resume_attempt(self.repo, self.store, self.orch_id, task.id))
        journal_resume(
            self.store, self.orch_id, task.id, "child_resume_skipped", reason
        )
        return False

    def _pending_conflict(self) -> ConflictInfo | None:
        record = self._load()
        task_id = record.integration.get("merging")
        if not task_id:
            return None
        paths = conflicted_paths(Path(record.integration["worktree"]))
        return conflict_info(record, task_id, paths)

    async def _merge_passed(self) -> ConflictInfo | None:
        for task_id in [t.id for t in self._load().tasks if t.status == "passed"]:
            conflict = await self._merge_one(task_id)
            if conflict is not None:
                return conflict
        return None

    async def _merge_one(self, task_id: str) -> ConflictInfo | None:
        try:
            outcome = begin_merge(self.repo, self.store, self.orch_id, task_id)
        except RuntimeError as exc:
            if not str(exc).startswith(GIT_MERGE_FAILED):
                raise
            self._merge_error(task_id, exc)
            return None
        if outcome.status != "conflict":
            outcome = await finish_merge(
                self.repo, self.store, self.orch_id, task_id, self.config
            )
        return self._after_merge(task_id, outcome)

    def _after_merge(self, task_id: str, outcome) -> ConflictInfo | None:
        record = self._load()
        if record.task(task_id).status == "merged":
            self.merged.append(task_id)
        elif outcome.status == "checks_failed":
            self._integration_failure(task_id, outcome.log)
        else:
            return conflict_info(record, task_id, outcome.conflicted_paths)
        return None

    def _merge_error(self, task_id: str, exc: RuntimeError) -> None:
        """A non-conflict git failure: keep its text as the failure's evidence."""
        log = self.store.dir_for(self.orch_id) / f"integration-{task_id}-error.log"
        log.write_text(f"{exc}\n", encoding="utf-8")
        self._integration_failure(task_id, log)

    def _integration_failure(self, task_id: str, log: Path | None) -> None:
        task = self._load().task(task_id)
        evidence = failure_evidence(self.repo, task, integration_log=log)
        failure = FailureInfo(task_id, "integration", tuple(evidence))
        record_task_failure(self.store, self.orch_id, failure, self.max_cycles)

    def _settle(self) -> TickResult:
        if self._load().phase != "blocked":
            self._launch_ready()
        return self._classify(self._load())

    def _classify(self, record: OrchestratorRecord) -> TickResult:
        running = any(t.status == "running" for t in record.tasks)
        if record.phase == "blocked":
            return self._result("waiting" if running else "blocked")
        if all(t.status == "merged" for t in record.tasks):
            return self._result("all_merged")
        failed = [t for t in record.tasks if t.status == "failed"]
        if failed:
            return self._result("failures", failures=self._failures(failed))
        if not running:
            pending = [t.id for t in record.tasks if t.status != "merged"]
            raise RuntimeError(
                f"Orchestrator {self.orch_id} is stalled: no task can run "
                f"(unmerged: {', '.join(pending)})."
            )
        return self._result("waiting")

    def _launch_ready(self) -> None:
        for task_id in tasks_to_launch(self._load()):
            run_id = launch_attempt(
                self.repo,
                self.store,
                self.orch_id,
                task_id,
                config=self.config,
                launcher=self.launcher,
            )
            if run_id:
                self.launched.append(task_id)
            else:
                failure = FailureInfo(task_id, "launch-failed", ())
                record_task_failure(self.store, self.orch_id, failure, self.max_cycles)

    @staticmethod
    def _failures(failed) -> tuple[FailureInfo, ...]:
        infos = []
        for task in failed:
            entry = last_failure(task)
            infos.append(
                FailureInfo(
                    task.id,
                    entry.get("cause", "child-failed"),
                    tuple(entry.get("evidence", ())),
                )
            )
        return tuple(infos)


async def tick(  # ruff: ignore[too-many-arguments] -- the pinned supervisor signature
    repo: Path,
    store: OrchestratorStore,
    orch_id: str,
    config: dict,
    *,
    launcher: Launcher = launch_background,
) -> TickResult:
    """Advance one orchestrator by one supervisor step."""
    return await _Supervisor(repo, store, orch_id, config, launcher).run()
