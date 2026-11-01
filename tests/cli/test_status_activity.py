import time

from meow.cli.status_cli import render
from meow.execution.run_state import RunStore


def _run(tmp_path):
    store = RunStore(tmp_path)
    record = store.create(
        source="prompt", request="x", repo=tmp_path, worktree=tmp_path, branch="b"
    )
    store.transition(record.id, "stage_started")
    return store, record


def test_status_shows_recent_agent_activity(tmp_path):
    store, record = _run(tmp_path)
    store.record_activity(record.id, "generator", "Bash")
    lines = render(store.load(record.id)).splitlines()
    checkpoint = next(i for i, x in enumerate(lines) if x.startswith("Last checkpoint"))
    assert lines[checkpoint + 1].startswith("Last agent activity: ")
    assert "(generator: Bash)" in lines[checkpoint + 1]
    assert "stalled" not in lines[checkpoint + 1]


def test_status_flags_a_stalled_run(tmp_path):
    store, record = _run(tmp_path)
    store.update_results(
        record.id,
        activity={"at": time.time() - 5000, "role": "reviewer", "event": "Read"},
    )
    output = render(store.load(record.id))
    assert "possibly stalled" in output
    assert "(reviewer: Read)" in output


def test_status_omits_activity_when_none_recorded(tmp_path):
    store, record = _run(tmp_path)
    assert "Last agent activity" not in render(store.load(record.id))
