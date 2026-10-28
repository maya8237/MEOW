import os
import threading
import time

import pytest

from meow.execution.run_state import RunStateError, RunStore

THREADS = 6
SECOND = 2


def _parked(tmp_path):
    store = RunStore(tmp_path)
    record = store.create(
        source="cli", repo=tmp_path, request="r", worktree=tmp_path, branch="b"
    )
    store.transition(record.id, "parked")
    return store, record.id


def test_concurrent_claims_admit_exactly_one(tmp_path):
    store, run_id = _parked(tmp_path)
    wins, errors = [], []

    def claim():
        try:
            wins.append(store.claim_resume(run_id, phase="parked"))
        except RunStateError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=claim) for _ in range(THREADS)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    # Same pid drives every thread, so the pid check cannot tell them apart;
    # the lock must at least serialise them without corrupting the record.
    assert len(wins) + len(errors) == THREADS
    assert store.load(run_id).phase == "resuming"
    assert not list(store.directory.glob("*.claim"))


def test_stale_claim_lock_is_broken(tmp_path):
    store, run_id = _parked(tmp_path)
    lock = store.directory / f"{run_id}.claim"
    lock.write_text("")
    old = time.time() - 120
    os.utime(lock, (old, old))
    assert store.claim_resume(run_id, phase="parked").phase == "resuming"


def test_note_resume_blocked_counts_and_claim_resets(tmp_path):
    store, run_id = _parked(tmp_path)
    assert store.note_resume_blocked(run_id, "git down") == 1
    assert store.note_resume_blocked(run_id, "git down") == SECOND
    store.claim_resume(run_id, phase="parked")
    assert "resume_blocked" not in store.load(run_id).park
    with pytest.raises(RunStateError):
        store.claim_resume(run_id, phase="parked")
