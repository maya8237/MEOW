"""
meow/plan_files.py

Plan/review file naming and lookup within a project's docs_dir: which file
is the latest sprint plan, which review file goes with a plan, and what
flavor (plan/prompt/gitlab) an existing review file is. Split out of
orchestrator.py, which re-exports these for compatibility, because this is
file-naming/lookup logic -- distinct from the generator<->reviewer
round-loop control flow orchestrator.py's own docstring says it holds.
"""

from pathlib import Path

from meow.agents.reviewer import (
    BRANCH_REVIEW_FILENAME,
    MR_REVIEW_FILENAME,
    PROMPT_REVIEW_FILENAME,
)


def _latest_plan_file(docs_dir: Path) -> Path:
    """The most recently modified sprint plan in docs_dir, excluding reviews.

    Excludes both `<plan>-review.md` verdicts and `cr`'s own free-standing
    `review.md` report -- neither is a Sprint Contract, so picking either up
    here would hand the reviewer a review to grade as if it were a plan.
    """
    candidates = [
        path for path in docs_dir.glob("*.md")
        if not path.name.endswith("review.md")
    ]
    if not candidates:
        raise FileNotFoundError(
            f"No plan file found in {docs_dir}. Run `meow plan "
            '"<feature>"` first, or pass --plan-file explicitly.'
        )
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _detect_review_flavor(review_file: Path) -> str:
    """Classify a review file by its filename -- meow's own three review
    verdict formats each have a distinct, deterministic naming convention,
    so no ambiguity and no guessing is needed here."""
    name = review_file.name
    if name == MR_REVIEW_FILENAME:
        return "gitlab"
    if name == PROMPT_REVIEW_FILENAME:
        return "prompt"
    if name == BRANCH_REVIEW_FILENAME:
        return "branch"
    if name.endswith("-review.md"):
        return "plan"
    raise ValueError(
        f"{review_file} doesn't look like a review file meow wrote "
        "(expected a name ending in 'review.md')."
    )


def _latest_review_file(docs_dir: Path) -> Path:
    """The most recently modified review file in docs_dir, of any flavor."""
    candidates = list(docs_dir.glob("*review.md"))
    if not candidates:
        raise FileNotFoundError(
            f"No review file found in {docs_dir}. Run `meow review`, "
            "`meow cr`, or `meow gitlab-review` first, or pass "
            "--review-file explicitly."
        )
    return max(candidates, key=lambda path: path.stat().st_mtime)
