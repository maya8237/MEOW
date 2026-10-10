import os
from pathlib import Path

import pytest

from meow.project.plan_state import PlanOwnedError, PlanStore, discover_plan


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


def test_discover_explicit_relative_plan_resolves_under_docs(tmp_path):
    docs = tmp_path / "docs"
    plan = _write(docs / "a.md")
    assert discover_plan(docs, Path("a.md")) == plan.resolve()


def test_discover_explicit_missing_plan_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="Plan file not found"):
        discover_plan(tmp_path, Path("missing.md"))


def test_discover_skips_reviews_and_tester_verdicts(tmp_path):
    docs = tmp_path / "docs"
    plan = _write(docs / "plan.md")
    review = _write(docs / "plan-review.md")
    tester = _write(docs / "plan-test.md")
    report = _write(docs / "review.md")
    for newer in (review, tester, report):
        os.utime(newer, (plan.stat().st_mtime + 10, plan.stat().st_mtime + 10))
    assert discover_plan(docs) == plan.resolve()


def test_discover_without_candidates_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="No plan file found"):
        discover_plan(tmp_path)


def test_discover_prefers_the_plan_linked_to_a_run(tmp_path):
    docs = tmp_path / "docs"
    older = _write(docs / "older.md")
    newer = _write(docs / "newer.md")
    os.utime(older, (1_000_000_000, 1_000_000_000))
    os.utime(newer, (2_000_000_000, 2_000_000_000))
    PlanStore(docs).transition(older, "in-progress", "run-7")

    assert discover_plan(docs, run_id="run-7") == older.resolve()
    assert discover_plan(docs) == newer.resolve()
