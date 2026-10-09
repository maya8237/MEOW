from pathlib import Path

import pytest

from meow.native.native_lint import LintOptions, lint
from meow.native.native_prepare import latest_plan, latest_review, verdict
from meow.native.native_prompt import role_prompt


def _docs(root: Path) -> Path:
    docs = root / ".meow" / "plans"
    docs.mkdir(parents=True)
    return docs


def test_verdict_reads_status_and_summary(tmp_path):
    review = tmp_path / "plan-review.md"
    review.write_text("SUMMARY: all criteria met\nSTATUS: PASS\n", encoding="utf-8")
    assert verdict(review) == {"status": "PASS", "summary": "all criteria met"}


def test_verdict_without_status_line_fails_closed(tmp_path):
    review = tmp_path / "plan-review.md"
    review.write_text("no verdict here\n", encoding="utf-8")
    assert verdict(review) == {"status": "FAIL", "summary": None}


def test_latest_plan_pairs_plan_with_its_review(tmp_path):
    docs = _docs(tmp_path)
    plan = docs / "feature.md"
    plan.write_text("# Feature\n", encoding="utf-8")

    result = latest_plan(tmp_path, tmp_path)

    assert result == {
        "plan_file": str(plan),
        "review_file": str(docs / "feature-review.md"),
    }


def test_latest_review_reports_plan_flavor(tmp_path):
    docs = _docs(tmp_path)
    review = docs / "feature-review.md"
    review.write_text("STATUS: PASS\n", encoding="utf-8")

    assert latest_review(tmp_path, tmp_path) == {
        "review_file": str(review),
        "flavor": "plan",
    }


def test_latest_review_without_reviews_raises(tmp_path):
    _docs(tmp_path)
    with pytest.raises(FileNotFoundError, match="No review file found"):
        latest_review(tmp_path, tmp_path)


def test_lint_is_clean_when_no_commands_are_configured(tmp_path):
    (tmp_path / "x.py").write_text("x = 1\n", encoding="utf-8")

    assert lint(tmp_path, tmp_path, LintOptions()) == {
        "clean": True,
        "blocking": [],
        "informational": [],
    }
    assert lint(tmp_path, tmp_path, LintOptions(file_path="x.py")) == {
        "clean": True,
        "problems": [],
    }


def test_role_prompt_rejects_unknown_role(tmp_path):
    with pytest.raises(ValueError, match="role must be one of"):
        role_prompt(tmp_path, tmp_path, "not-a-role")


def test_generator_prompt_requires_a_plan(tmp_path):
    with pytest.raises(ValueError, match="needs --plan"):
        role_prompt(tmp_path, tmp_path, "generator")


def test_explorer_prompt_is_a_system_prompt_without_query(tmp_path):
    result = role_prompt(tmp_path, tmp_path, "explorer")
    assert result["system_prompt"]
    assert result["query"] is None
