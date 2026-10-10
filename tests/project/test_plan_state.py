from pathlib import Path

import pytest

from meow.project.plan_state import PlanOwnedError, PlanStore


def _write(path: Path, text: str = "# Plan\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_read_without_sidecar_reports_draft(tmp_path):
    plan = _write(tmp_path / "feature.md")
    meta = PlanStore(tmp_path).read(plan)
    assert meta.lifecycle == "draft"
    assert meta.plan == str(plan.resolve())
    assert meta.run_id is None


def test_transition_persists_lifecycle_run_and_note(tmp_path):
    plan = _write(tmp_path / "feature.md")
    store = PlanStore(tmp_path)

    meta = store.transition(plan, "in-progress", "run-1", note="started")

    assert meta.lifecycle == "in-progress"
    reread = store.read(plan)
    assert reread.lifecycle == "in-progress"
    assert reread.run_id == "run-1"
    assert reread.discoveries == ["started"]
    assert (tmp_path / "feature.md.state.json").is_file()


def test_transition_cannot_move_lifecycle_backwards(tmp_path):
    plan = _write(tmp_path / "feature.md")
    store = PlanStore(tmp_path)
    store.transition(plan, "complete")
    with pytest.raises(ValueError, match="cannot move backwards"):
        store.transition(plan, "draft")


def test_takeover_redrafts_plan_left_behind_by_a_finished_run(tmp_path):
    plan = _write(tmp_path / "feature.md")
    store = PlanStore(tmp_path)
    store.transition(plan, "draft", "run-1")
    store.transition(plan, "in-progress", "run-1")

    meta = store.transition(plan, "draft", "run-2", takeover=True)

    assert meta.lifecycle == "draft"
    assert meta.run_id == "run-2"
    assert store.read(plan).lifecycle == "draft"


def test_redraft_without_takeover_is_refused_for_another_runs_plan(tmp_path):
    plan = _write(tmp_path / "feature.md")
    store = PlanStore(tmp_path)
    store.transition(plan, "in-progress", "run-1")
    with pytest.raises(ValueError, match="cannot move backwards"):
        store.transition(plan, "draft", "run-2")


def test_claim_takes_over_only_from_a_finished_owner(tmp_path):
    plan = _write(tmp_path / "feature.md")
    store = PlanStore(tmp_path)
    store.transition(plan, "in-progress", "run-1")

    meta = store.claim(plan, "run-2", owner_finished=lambda owner: owner == "run-1")

    assert meta.run_id == "run-2"
    assert meta.lifecycle == "draft"


def test_claim_blocks_when_owner_cannot_be_verified(tmp_path):
    plan = _write(tmp_path / "feature.md")
    store = PlanStore(tmp_path)
    store.transition(plan, "in-progress", "run-1")

    with pytest.raises(PlanOwnedError, match="run run-1"):
        store.claim(plan, "run-2", owner_finished=lambda owner: False)
    assert store.read(plan).run_id == "run-1"


def test_claim_by_the_owning_run_keeps_its_lifecycle(tmp_path):
    plan = _write(tmp_path / "feature.md")
    store = PlanStore(tmp_path)
    store.claim(plan, "run-1", owner_finished=lambda owner: False)
    store.transition(plan, "in-progress", "run-1")

    meta = store.claim(plan, "run-1", owner_finished=lambda owner: False)

    assert meta.run_id == "run-1"
    assert meta.lifecycle == "in-progress"
    assert store.read(plan).lifecycle == "in-progress"


def test_same_run_still_cannot_move_lifecycle_backwards(tmp_path):
    plan = _write(tmp_path / "feature.md")
    store = PlanStore(tmp_path)
    store.transition(plan, "in-progress", "run-1")
    with pytest.raises(ValueError, match="cannot move backwards"):
        store.transition(plan, "draft", "run-1")


def test_transition_rejects_unknown_lifecycle(tmp_path):
    plan = _write(tmp_path / "feature.md")
    with pytest.raises(ValueError, match="invalid plan lifecycle"):
        PlanStore(tmp_path).transition(plan, "done")


def test_read_treats_corrupt_sidecar_as_absent(tmp_path):
    plan = _write(tmp_path / "feature.md")
    _write(tmp_path / "feature.md.state.json", "{not json")
    assert PlanStore(tmp_path).read(plan).lifecycle == "draft"


def test_read_treats_unknown_lifecycle_sidecar_as_absent(tmp_path):
    plan = _write(tmp_path / "feature.md")
    _write(
        tmp_path / "feature.md.state.json",
        '{"plan": "feature.md", "lifecycle": "done"}',
    )
    assert PlanStore(tmp_path).read(plan).lifecycle == "draft"


def test_discoveries_keep_only_the_latest_twenty_notes(tmp_path):
    plan = _write(tmp_path / "feature.md")
    store = PlanStore(tmp_path)
    for index in range(25):
        store.transition(plan, "draft", note=f"n{index}")
    notes = store.read(plan).discoveries
    note_limit = 20
    assert len(notes) == note_limit
    assert notes[0] == "n5"
    assert notes[-1] == "n24"
