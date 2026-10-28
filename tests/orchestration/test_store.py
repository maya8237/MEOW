"""The orchestrator journal is atomic and its driver lock is exclusive."""

import json
import subprocess
import sys
import time

import pytest

from meow.execution.orchestration import store as store_module
from meow.execution.orchestration.model import OrchestratorTask
from meow.execution.orchestration.store import (
    NATIVE_HEARTBEAT_SECONDS,
    DriverBusyError,
    DriverLock,
    OrchestratorStateError,
    OrchestratorStore,
)

MISSING_HEARTBEAT_SECONDS = NATIVE_HEARTBEAT_SECONDS + 1
ID_LENGTH = 12


def make_store(tmp_path):
    return OrchestratorStore(tmp_path)


def create(store, **overrides):
    values = {
        "objective": "Ship it",
        "source": "objective",
        "base_branch": "main",
        "mode": "cli",
        "max_parallel": 3,
        "options": {"test": False, "pr": True},
    }
    return store.create(**{**values, **overrides})


def test_create_starts_decomposing_and_writes_state(tmp_path):
    store = make_store(tmp_path)
    record = create(store)
    assert record.phase == "decomposing"
    assert len(record.id) == ID_LENGTH
    path = tmp_path / ".meow" / "orchestrators" / record.id / "state.json"
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["objective"] == "Ship it"
    assert store.cancel_marker(record.id) == path.parent / "cancel"


def test_round_trip_preserves_tasks(tmp_path):
    store = make_store(tmp_path)
    record = create(store, source="jira")
    record.tasks = [
        OrchestratorTask(
            "api",
            "Build api",
            source_ref="PROJ-1",
            depends_on=[],
            expected_paths=["src/api"],
            status="running",
            branch="orchestrator/x-api",
            worktree="/tmp/wt",
            run_ids=["r" * 32],
            cycles=2,
            history=[{"action": "retry"}],
            next_attempt={"resume_at": "review", "plan_file": "p.md"},
        ),
        OrchestratorTask("ui", "Build ui", depends_on=["api"]),
    ]
    record.blocker = {"task_id": "api", "cause": "needs-human"}
    saved = store.save(record)
    assert store.load(record.id) == saved
    assert store.load(record.id).tasks[0].next_attempt == {
        "resume_at": "review",
        "plan_file": "p.md",
    }


def test_save_refreshes_updated_at_and_leaves_no_temp_files(tmp_path):
    store = make_store(tmp_path)
    record = create(store)
    first = record.updated_at
    time.sleep(0.01)
    store.save(record)
    assert store.load(record.id).updated_at > first
    assert list(store.dir_for(record.id).glob("*.tmp")) == []


def test_update_applies_the_mutation_and_saves(tmp_path):
    store = make_store(tmp_path)
    record = create(store)

    def advance(item):
        item.phase = "running"

    updated = store.update(record.id, advance)
    assert updated.phase == "running"
    assert store.load(record.id).phase == "running"


def test_corrupt_state_raises_state_error(tmp_path):
    store = make_store(tmp_path)
    record = create(store)
    (store.dir_for(record.id) / "state.json").write_text("{", encoding="utf-8")
    with pytest.raises(OrchestratorStateError, match="corrupt"):
        store.load(record.id)


@pytest.mark.parametrize(
    "patch",
    [
        {"phase": "exploded"},
        {"source": "carrier-pigeon"},
        {"mode": "telepathy"},
        {"schema_version": 2},
        {"surprise": True},
    ],
)
def test_invalid_state_fields_raise_state_error(tmp_path, patch):
    store = make_store(tmp_path)
    record = create(store)
    path = store.dir_for(record.id) / "state.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**raw, **patch}), encoding="utf-8")
    with pytest.raises(OrchestratorStateError, match="corrupt"):
        store.load(record.id)


def test_invalid_task_status_raises_state_error(tmp_path):
    store = make_store(tmp_path)
    record = create(store)
    record.tasks = [OrchestratorTask("api", "r", status="weird")]
    store.save(record)
    with pytest.raises(OrchestratorStateError, match="unknown task status"):
        store.load(record.id)


def test_unknown_and_missing_ids_are_rejected(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(OrchestratorStateError, match="Unknown orchestrator"):
        store.load("../x")
    with pytest.raises(OrchestratorStateError, match="missing"):
        store.load("0123456789ab")


def test_records_skip_corrupt_ones_and_latest_is_most_recent(tmp_path):
    store = make_store(tmp_path)
    assert store.records() == []
    with pytest.raises(OrchestratorStateError, match="No orchestrators"):
        store.latest()
    older = create(store)
    broken = create(store)
    newer = create(store)
    (store.dir_for(broken.id) / "state.json").write_text("{", encoding="utf-8")
    store.save(newer)
    assert {record.id for record in store.records()} == {older.id, newer.id}
    assert store.latest().id == newer.id


def test_cli_lock_blocks_second_driver_and_reclaims_dead_pid(tmp_path, monkeypatch):
    directory = tmp_path / "orch"
    first = DriverLock(directory, mode="cli")
    assert first.acquire() is True
    assert first.holder()["mode"] == "cli"
    assert DriverLock(directory, mode="cli").acquire() is False
    with (
        pytest.raises(DriverBusyError, match="CLI process"),
        DriverLock(directory, mode="cli"),
    ):
        pass
    monkeypatch.setattr(store_module, "_pid_is_alive", lambda _pid: False)
    assert DriverLock(directory, mode="cli").acquire() is True


def test_cli_lock_held_by_a_reused_pid_is_reclaimed(tmp_path, monkeypatch):
    """The recorded PID is alive, but it is another process now."""
    directory = tmp_path / "orch"
    assert DriverLock(directory, mode="cli").acquire() is True
    assert DriverLock(directory, mode="cli").holder()["start_identity"]
    monkeypatch.setattr(store_module, "process_identity", lambda _pid: "other")
    assert DriverLock(directory, mode="cli").acquire() is True


def test_cli_lock_without_a_recorded_identity_still_checks_the_pid(tmp_path):
    directory = tmp_path / "orch"
    lock = DriverLock(directory, mode="cli")
    assert lock.acquire() is True
    holder = lock.holder()
    holder.pop("start_identity")
    lock.path.write_text(json.dumps(holder), encoding="utf-8")
    assert DriverLock(directory, mode="cli").acquire() is False


def test_cli_lock_stays_held_when_identity_cannot_be_read(tmp_path, monkeypatch):
    """An unreadable start time for a live PID is not a dead holder."""
    directory = tmp_path / "orch"
    assert DriverLock(directory, mode="cli").acquire() is True
    monkeypatch.setattr(store_module, "process_identity", lambda _pid: None)
    assert DriverLock(directory, mode="cli").acquire() is False


def test_release_frees_the_lock(tmp_path):
    directory = tmp_path / "orch"
    with DriverLock(directory, mode="cli"):
        assert (directory / "driver.lock").is_file()
    assert not (directory / "driver.lock").exists()
    assert DriverLock(directory, mode="cli").acquire() is True


def test_live_cli_lock_blocks_native(tmp_path):
    directory = tmp_path / "orch"
    assert DriverLock(directory, mode="cli").acquire() is True
    assert DriverLock(directory, mode="native").acquire() is False


def test_live_native_lock_blocks_cli(tmp_path):
    directory = tmp_path / "orch"
    assert DriverLock(directory, mode="native").acquire() is True
    assert DriverLock(directory, mode="cli").acquire() is False


def test_native_lock_reclaimable_after_heartbeat_timeout(tmp_path, monkeypatch):
    directory = tmp_path / "orch"
    assert DriverLock(directory, mode="native").acquire() is True
    monkeypatch.setattr(store_module, "_pid_is_alive", lambda pid: False)
    real_now = time.time()
    monkeypatch.setattr(
        store_module.time, "time", lambda: real_now + MISSING_HEARTBEAT_SECONDS
    )
    assert DriverLock(directory, mode="cli").acquire() is True
    assert DriverLock(directory, mode="cli").holder()["mode"] == "cli"


def test_native_lock_can_be_taken_over_by_another_native_call(tmp_path):
    directory = tmp_path / "orch"
    first = DriverLock(directory, mode="native")
    second = DriverLock(directory, mode="native")
    assert first.acquire() is True
    assert second.acquire() is True
    second.release()
    assert not (directory / "driver.lock").exists()
    first.release()


def test_live_native_operation_blocks_another_process_even_after_timeout(tmp_path):
    directory = tmp_path / "orch"
    first = DriverLock(directory, mode="native")
    assert first.acquire()
    holder = first.holder()
    holder["heartbeat_at"] = 0
    first.path.write_text(json.dumps(holder))
    script = (
        "from pathlib import Path; "
        "from meow.execution.orchestration.store import DriverLock; "
        "import sys; "
        "lock = DriverLock(Path(sys.argv[1]), mode=sys.argv[2]); "
        "print(lock.acquire())"
    )
    for mode in ("native", "cli"):
        result = subprocess.run(
            [sys.executable, "-c", script, str(directory), mode],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "False"


def test_concurrent_native_helpers_cannot_both_claim_dead_holder(tmp_path):
    directory = tmp_path / "orch"
    directory.mkdir()
    (directory / "driver.lock").write_text(
        json.dumps({"mode": "native", "pid": 2**30, "heartbeat_at": 0})
    )
    script = """
import sys
import time
from pathlib import Path
from meow.execution.orchestration.store import DriverLock

class SlowClaim(DriverLock):
    def _take_native(self, holder):
        # Both contenders can read the old holder before either replaces it.
        time.sleep(0.25)
        return super()._take_native(holder)

lock = SlowClaim(Path(sys.argv[1]), mode="native")
print("ready", flush=True)
sys.stdin.readline()
print(lock.acquire(), flush=True)
sys.stdin.readline()  # Keep the winning process alive during the other claim.
"""
    children = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(directory)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    try:
        for child in children:
            assert child.stdout.readline().strip() == "ready"
        for child in children:
            child.stdin.write("claim\n")
            child.stdin.flush()
        claimed = [child.stdout.readline().strip() for child in children]
        errors = [
            child.stderr.read() if child.poll() is not None else ""
            for child in children
        ]
        assert sorted(claimed) == ["False", "True"], errors
    finally:
        for child in children:
            child.communicate("exit\n", timeout=10)


def test_native_heartbeat_refreshes_the_timestamp(tmp_path, monkeypatch):
    directory = tmp_path / "orch"
    lock = DriverLock(directory, mode="native")
    assert lock.acquire() is True
    started = lock.holder()["heartbeat_at"]
    monkeypatch.setattr(store_module.time, "time", lambda: started + 100)
    lock.heartbeat()
    assert lock.holder()["heartbeat_at"] == started + 100


def test_unreadable_fresh_lock_blocks_until_it_goes_stale(tmp_path, monkeypatch):
    directory = tmp_path / "orch"
    directory.mkdir()
    lock_file = directory / "driver.lock"
    lock_file.write_text("not json", encoding="utf-8")
    assert DriverLock(directory, mode="cli").acquire() is False
    real_now = time.time()
    monkeypatch.setattr(store_module.time, "time", lambda: real_now + 3600)
    assert DriverLock(directory, mode="cli").acquire() is True
