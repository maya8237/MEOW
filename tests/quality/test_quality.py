import json
from types import SimpleNamespace

import pytest

from meow.execution.orchestrator import _capture_quality
from meow.project.prompts import _verdict_format
from meow.infrastructure.quality import (
    QualityStoreError,
    extract_concern_candidates,
    load_concerns,
    record_concerns,
    relevant_concerns,
)


def _candidate(
    path="module.py",
    evidence="duplicate check",
    impact="maintenance",
    follow_up="consolidate checks",
):
    return {
        "path": path,
        "evidence": evidence,
        "impact": impact,
        "follow_up": follow_up,
    }


def test_record_requires_repository_evidence_and_deduplicates(tmp_path):
    (tmp_path / "module.py").write_text("duplicate check\n")
    first = record_concerns(tmp_path, "run-1", [_candidate(), _candidate()])
    second = record_concerns(tmp_path, "run-2", [_candidate()])
    assert len(first) == len(second) == 1
    assert first[0].id == second[0].id
    assert second[0].first_run == "run-1"
    assert second[0].last_run == "run-2"
    assert len(load_concerns(tmp_path)) == 1


def test_record_rejects_missing_evidence_and_escape(tmp_path):
    (tmp_path / "module.py").write_text("different text\n")
    assert record_concerns(tmp_path, "run", [_candidate()]) == []
    assert record_concerns(tmp_path, "run", [_candidate("../other.py")]) == []


def test_changed_location_becomes_stale_and_is_not_relevant(tmp_path):
    path = tmp_path / "module.py"
    path.write_text("duplicate check\n")
    record_concerns(tmp_path, "run-1", [_candidate()])
    path.write_text("fixed\n")
    assert relevant_concerns(tmp_path, ["module.py"]) == []
    assert load_concerns(tmp_path)[0].state == "stale"


def test_corrupt_store_is_preserved(tmp_path):
    store = tmp_path / ".meow" / "quality-concerns.json"
    store.parent.mkdir()
    store.write_text("{broken")
    with pytest.raises(QualityStoreError):
        record_concerns(tmp_path, "run", [])
    assert store.read_text() == "{broken"


def test_relevant_concerns_excludes_unrelated_paths(tmp_path):
    (tmp_path / "module.py").write_text("duplicate check\n")
    record_concerns(tmp_path, "run", [_candidate()])
    assert len(relevant_concerns(tmp_path, ["module.py"])) == 1
    assert relevant_concerns(tmp_path, ["other.py"]) == []


def test_extract_requires_explicit_structured_concern():
    line = "QUALITY_CONCERN: " + json.dumps(_candidate(follow_up="consolidate"))
    assert extract_concern_candidates("STATUS: PASS\n" + line) == [
        {
            "path": "module.py",
            "evidence": "duplicate check",
            "impact": "maintenance",
            "follow_up": "consolidate",
        }
    ]
    assert extract_concern_candidates("This seems fragile.") == []


def test_reviewer_concern_is_saved_only_with_run_journal(tmp_path):
    (tmp_path / "module.py").write_text("duplicate check\n")
    sprint = SimpleNamespace(
        config={"_run_journal": (None, "run-1")},
        repo_dir=tmp_path,
        active_working_dir=lambda: tmp_path,
    )
    verdict = "STATUS: PASS\nQUALITY_CONCERN: " + json.dumps(
        _candidate(follow_up="consolidate")
    )
    _capture_quality(sprint, verdict)
    assert len(load_concerns(tmp_path)) == 1


def test_concern_store_persists_across_separate_worktrees(tmp_path):
    main = tmp_path / "main"
    worktree = tmp_path / "worktree"
    main.mkdir()
    worktree.mkdir()
    (worktree / "module.py").write_text("duplicate check\n")
    record_concerns(main, "run-1", [_candidate()], evidence_root=worktree)
    assert len(load_concerns(main)) == 1
    assert not (worktree / ".meow" / "quality-concerns.json").exists()


def test_review_prompt_requests_only_evidence_backed_advisory_concerns(tmp_path):
    instruction = _verdict_format(tmp_path / "review.md", "criterion")
    assert "QUALITY_CONCERN:" in instruction
    assert "exact code text" in instruction
    assert "advisory" in instruction
