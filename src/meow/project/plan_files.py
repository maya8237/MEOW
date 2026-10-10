"""
meow/plan_files.py

Plan/review file naming and lookup within a project's docs_dir: which file
is the latest sprint plan, which review file goes with a plan, and what
flavor (plan/prompt/gitlab/github) an existing review file is. This is
file-naming/lookup logic -- distinct from the generator<->reviewer
round-loop control flow orchestrator.py's own docstring says it holds.
"""

from pathlib import Path

from meow.agents.reviewer import (
    BRANCH_REVIEW_FILENAME,
    GITHUB_REVIEW_FILENAME,
    GITLAB_REVIEW_FILENAME,
    PROMPT_REVIEW_FILENAME,
    REVIEW_FILE_PATTERN,
)


def planned_plan_file(docs_dir: Path, feature_name: str | None) -> Path:
    """Where the planner writes the plan for ``feature_name``."""
    return docs_dir / (f"{feature_name}.md" if feature_name else "plan.md")


def reject_report_name(feature_name: str | None) -> None:
    """Refuse names whose plan file would read as a report file.

    ``add-code-review.md`` and ``foo-test.md`` are skipped by the latest-plan
    lookup, and ``foo-review.md`` / ``foo-test.md`` are the names plan ``foo``'s
    reviewer and tester write, so such a plan could neither be found nor kept.
    """
    if feature_name is None:
        return
    lowered = feature_name.lower()
    if lowered.endswith(("review", "-test")):
        raise ValueError(
            f"{feature_name!r} can't be a feature name: names ending in "
            "'review' or '-test' collide with review and tester report files. "
            "Pick a different --name."
        )


def _latest_plan_file(docs_dir: Path) -> Path:
    """The most recently modified sprint plan in docs_dir, excluding reviews.

    Excludes `<plan>-review.md` verdicts, `<plan>-test.md` tester verdicts,
    and `cr`'s own free-standing `review.md` report -- none is a Sprint
    Contract, so picking one up here would hand it back as if it were a plan.
    """
    candidates = [
        path
        for path in docs_dir.glob("*.md")
        if not path.name.endswith(("review.md", "-test.md"))
    ]
    if not candidates:
        raise FileNotFoundError(
            f"No plan file found in {docs_dir}. Run `meow plan "
            '"<feature>"` first, or pass --plan explicitly.'
        )
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _detect_review_flavor(review_file: Path) -> str:
    """Classify a review file by its filename -- meow's own three review
    verdict formats each have a distinct, deterministic naming convention,
    so no ambiguity and no guessing is needed here."""
    name = review_file.name
    match = REVIEW_FILE_PATTERN.fullmatch(name)
    if match:
        return match.group(1)
    flavor = {
        GITLAB_REVIEW_FILENAME: "gitlab",
        GITHUB_REVIEW_FILENAME: "github",
        PROMPT_REVIEW_FILENAME: "prompt",
        BRANCH_REVIEW_FILENAME: "branch",
    }.get(name)
    if flavor:
        return flavor
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
            f"No review file found in {docs_dir}. Run `meow review` "
            "first, or pass --review-file explicitly."
        )
    return max(candidates, key=lambda path: path.stat().st_mtime)
