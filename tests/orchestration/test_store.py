"""The orchestrator journal is atomic and its driver lock is exclusive."""

import json
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
