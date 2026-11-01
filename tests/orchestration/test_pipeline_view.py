"""The orchestrator-backed store the pipeline loop runs against."""

import pytest

from meow.execution.orchestration.pipeline_view import OrchestratorPipelineStore
from meow.infrastructure.cancellation import (
    RunCancelled,
    cancel_requested,
    check_cancel,
)
from tests.orchestration.fakes import create_orchestrator, make_repository


def _view(tmp_path):
    repo, _origin = make_repository(tmp_path)
    store, orch_id = create_orchestrator(repo, [])
    return store, OrchestratorPipelineStore(store), orch_id


def test_load_exposes_branch_worktree_and_pushed_commit(tmp_path):
    store, view, orch_id = _view(tmp_path)
    store.update(orch_id, lambda r: r.delivery.update(pushed_commit="abc"))
    loaded = view.load(orch_id)
    record = store.load(orch_id)
    assert loaded.delivery["branch"] == record.integration["branch"]
    assert loaded.delivery["commit"] == "abc"
    assert loaded.worktree == record.integration["worktree"]


def test_transition_merges_delivery_and_keeps_phase(tmp_path):
    store, view, orch_id = _view(tmp_path)
    view.transition(orch_id, "pipeline_waiting", delivery={"pipeline": {"rounds": 1}})
    record = store.load(orch_id)
    assert record.delivery["pipeline"] == {"rounds": 1}
    assert record.phase == "running"


def test_cancel_marker_is_the_orchestrators(tmp_path):
    store, view, orch_id = _view(tmp_path)
    assert not cancel_requested(view, orch_id)
    store.cancel_marker(orch_id).write_text("x")
    assert cancel_requested(view, orch_id)
    with pytest.raises(RunCancelled):
        check_cancel(view, orch_id)
    assert store.load(orch_id).phase == "cancelled"
