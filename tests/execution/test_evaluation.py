from pathlib import Path

import pytest

from meow.evaluation import evaluate_run
from meow.execution.run_state import RunStateError, RunStore


def _run(repo: Path, branch: str = "feature"):
    store = RunStore(repo)
    return store.create(
        source="prompt",
        request="add a feature",
        repo=repo,
        worktree=repo,
        branch=branch,
    )


def test_fresh_run_reports_every_missing_evidence_source(tmp_path):
    record = _run(tmp_path)

    report = evaluate_run(tmp_path)

    assert report.run_id == record.id
    assert report.verdict == "created"
    assert report.observed["delivery"] == "unavailable"
    assert report.unavailable == (
        "SDK usage/cost was not recorded",
        "tester evidence",
        "browser evidence",
        "plan reference",
    )
    assert report.comparison is None


def test_report_uses_stored_verdict_evidence_and_transitions(tmp_path):
    record = _run(tmp_path)
    plan = tmp_path / "plan.md"
    review = tmp_path / "plan-review.md"
    RunStore(tmp_path).transition(
        record.id,
        "complete",
        plan_file=str(plan),
        review_file=str(review),
        usage={"input_tokens": 3},
        results={
            "verdict": "PASS",
            "tester": {"status": "PASS"},
            "browser": {"status": "PASS"},
        },
    )

    report = evaluate_run(tmp_path, run_id=record.id)

    assert report.verdict == "PASS"
    assert report.observed["tester"] == {"status": "PASS"}
    assert report.observed["browser"] == {"status": "PASS"}
    assert report.unavailable == ()
    assert report.evidence == (
        str(plan),
        str(review),
        "transition:created",
        "transition:complete",
    )


def test_render_includes_judgments_only_when_verbose(tmp_path):
    record = _run(tmp_path)
    report = evaluate_run(tmp_path, run_id=record.id)

    terse = report.render()
    verbose = report.render(verbose=True)

    assert terse.startswith(f"Run {record.id}: created")
    assert "Judgment correctness" not in terse
    assert "Judgment correctness: observed from stored verdict and gates" in verbose
    assert "Unavailable: plan reference" in terse


def test_comparison_is_available_for_compatible_runs(tmp_path):
    first = _run(tmp_path)
    second = _run(tmp_path)

    report = evaluate_run(tmp_path, run_id=first.id, compare_ids=(second.id,))

    assert report.comparison == {"status": "available"}


def test_comparison_is_unavailable_across_branches(tmp_path):
    first = _run(tmp_path, branch="feature")
    other = _run(tmp_path, branch="other")

    report = evaluate_run(tmp_path, run_id=first.id, compare_ids=(other.id,))

    assert report.comparison == {
        "status": "unavailable",
        "reason": "revision, branch, or configuration evidence differs",
    }


def test_unknown_run_raises_state_error(tmp_path):
    with pytest.raises(RunStateError):
        evaluate_run(tmp_path, run_id="does-not-exist")
