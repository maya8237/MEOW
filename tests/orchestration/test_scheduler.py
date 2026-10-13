from pathlib import Path

from meow.execution.orchestration.model import OrchestratorRecord, OrchestratorTask
from meow.execution.orchestration.scheduler import (
    ready_tasks,
    tasks_to_launch,
    transitive_dependents,
)
from tests.orchestration.conftest import new_orchestrator


def record_with(
    tmp_path: Path, tasks: list[OrchestratorTask], **options
) -> OrchestratorRecord:
    store, orch_id = new_orchestrator(tmp_path, tasks, with_branch=False, **options)
    return store.load(orch_id)


def task(task_id: str, *depends_on: str, status: str = "pending") -> OrchestratorTask:
    return OrchestratorTask(
        task_id, f"do {task_id}", depends_on=list(depends_on), status=status
    )


def test_transitive_dependents_counts_indirect_dependents():
    tasks = [task("a"), task("b", "a"), task("c", "b"), task("d", "a"), task("e")]

    assert transitive_dependents(tasks) == {"a": 3, "b": 1, "c": 0, "d": 0, "e": 0}


def test_ready_requires_all_dependencies_merged(tmp_path):
    record = record_with(
        tmp_path,
        [
            task("a", status="merged"),
            task("b"),
            task("c", "a", "b"),
            task("d", "b", status="passed"),
        ],
    )

    assert ready_tasks(record) == ["b"]

    record.task("b").status = "merged"

    assert ready_tasks(record) == ["c"]


def test_ready_ignores_tasks_that_are_not_pending(tmp_path):
    record = record_with(
        tmp_path,
        [
            task("a", status="running"),
            task("b", status="failed"),
            task("c", status="blocked"),
            task("d", status="passed"),
        ],
    )

    assert ready_tasks(record) == []


def test_ready_orders_by_transitive_dependents_then_declaration(tmp_path):
    record = record_with(
        tmp_path,
        [task("d"), task("e"), task("a"), task("b", "a"), task("c", "b")],
    )

    assert ready_tasks(record) == ["a", "d", "e"]


def test_launch_respects_max_parallel(tmp_path):
    tasks = [task("r", status="running")] + [task(f"t{i}") for i in range(5)]
    record = record_with(tmp_path, tasks, max_parallel=3)

    assert tasks_to_launch(record) == ["t0", "t1"]


def test_no_launch_when_every_slot_is_taken(tmp_path):
    tasks = [task(f"r{i}", status="running") for i in range(3)] + [task("t")]
    record = record_with(tmp_path, tasks, max_parallel=3)

    assert tasks_to_launch(record) == []


def test_no_launch_while_blocked(tmp_path):
    record = record_with(tmp_path, [task("a")])
    record.blocker = {
        "task_id": "a",
        "cause": "needs-human",
        "evidence": [],
        "needed": "credentials",
    }

    assert tasks_to_launch(record) == []


def test_no_launch_outside_running_phase(tmp_path):
    record = record_with(tmp_path, [task("a")], phase="reviewing")

    assert tasks_to_launch(record) == []
