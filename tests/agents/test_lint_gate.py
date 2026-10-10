import pytest

from meow.agents.reviewer import ReviewerAgent, _verdict_status
from meow.infrastructure.lint import LintGateEvidence

BLOCKING = LintGateEvidence(blocking=("$ ruff check\nfailed",))


@pytest.mark.parametrize(
    "verdict",
    [
        "SUMMARY: ok\nSTATUS: PASS",
        "SUMMARY: ok\nSTATUS: PASS - all criteria met\ncriterion: PASS",
        "SUMMARY: ok\n  STATUS:   PASS",
        "SUMMARY: no status line",
    ],
)
def test_blocking_lint_makes_the_persisted_verdict_fail(tmp_path, verdict):
    review_file = tmp_path / "plan-review.md"

    status, text = ReviewerAgent._apply_lint_gate(
        "PASS", verdict, BLOCKING, review_file
    )

    assert status == "FAIL"
    assert _verdict_status(text) == "FAIL"
    assert _verdict_status(review_file.read_text(encoding="utf-8")) == "FAIL"
    assert "Blocking lint failures" in text
