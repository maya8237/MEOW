"""A huge diff must not overflow the reviewer's context window."""

from meow.agents import reviewer

HUGE = "+x\n" * 2_000_000  # a ~6 MB lockfile or generated file


def test_branch_review_prompt_is_bounded():
    text = reviewer.branch_review_query("main", "feature", HUGE, "lint ok")
    assert len(text) < reviewer.MAX_CONTEXT_CHARS * 2
    assert "truncated" in text
    assert "lint ok" in text  # the tail sections survive


def test_prompt_review_git_context_is_bounded():
    text = reviewer.prompt_review_query(
        "do it", HUGE, has_diff=True, docs_dir="docs", lint_report="lint ok"
    )
    assert len(text) < reviewer.MAX_CONTEXT_CHARS * 2
    assert "lint ok" in text


def test_remote_review_prompt_is_bounded():
    text = reviewer.remote_review_query("Merge request", "t", "d", HUGE)
    assert len(text) < reviewer.MAX_CONTEXT_CHARS * 2


def test_small_inputs_are_untouched():
    text = reviewer.branch_review_query("main", "feature", "+a\n", "lint ok")
    assert "truncated" not in text and "+a" in text
