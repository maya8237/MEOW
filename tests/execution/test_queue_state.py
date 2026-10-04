from meow.execution.queue_state import QueueStateError, QueueStore, QueueWorkerLock


def test_queue_persists_fifo_items_and_claims_once(tmp_path):
    store = QueueStore(tmp_path)
    first = store.enqueue("first")
    store.enqueue("second")

    assert [item.request for item in store.pending()] == ["first", "second"]
    claimed = store.claim(first.id)
    assert claimed.status == "running"
    assert claimed.attempts == 1
    assert [item.request for item in store.pending()] == ["second"]
    try:
        store.claim(first.id)
    except QueueStateError:
        pass
    else:
        raise AssertionError("a claimed item must not be claimable twice")


def test_worker_lock_allows_one_owner(tmp_path):
    first = QueueWorkerLock(tmp_path / ".meow" / "queue")
    second = QueueWorkerLock(tmp_path / ".meow" / "queue")
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_malformed_queue_is_not_silently_ignored(tmp_path):
    path = tmp_path / ".meow" / "queue"
    path.mkdir(parents=True)
    (path / "queue.json").write_text("not json", encoding="utf-8")
    try:
        QueueStore(tmp_path).list()
    except QueueStateError:
        pass
    else:
        raise AssertionError("malformed queue state must be reported")


def test_worker_lock_reclaims_definitely_dead_pid(tmp_path):
    path = tmp_path / ".meow" / "queue"
    path.mkdir(parents=True)
    (path / "worker.lock").write_text('{"pid": 999999999}', encoding="utf-8")
    lock = QueueWorkerLock(path)
    assert lock.acquire() is True
    lock.release()
