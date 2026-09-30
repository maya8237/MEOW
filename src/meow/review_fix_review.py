"""
meow/review_fix_review.py

`meow review-fix-review`'s top-level flow: fix and re-review an existing
review verdict until it passes. Split out of `orchestrator.py`, which holds
only the shared generator<->reviewer round-loop engine (`_run_review_rounds`,
`_run_prompt_fix_rounds`) this module reuses, the same way
`issue_solver.py`/`gitlab_reviewer.py`/`lint_fix.py` each own their own
CLI-facing flow instead of folding it into the engine module.
"""

from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.reviewer import ReviewerAgent, _verdict_status
from meow.config import load_config
from meow.lint import describe_lint_plan
from meow.logging import get_logger
from meow.orchestrator import _run_prompt_fix_rounds, _run_review_rounds
from meow.plan_files import _detect_review_flavor, _latest_review_file
from meow.sprint import build_sprint

logger = get_logger(__name__)


async def run_review_fix_review(  # ruff: ignore[too-many-statements] -- each flavor's branch is already only a few lines; splitting further would not leave a genuinely reusable, independently-testable piece
    working_dir: Path, prompt: str, review_file: Path | None = None
) -> None:
    """Fix and re-review an existing review verdict until it passes.

    Reuses `_run_review_rounds`'s review-then-fix loop for plan-based
    review files (its GeneratorAgent genuinely has a Sprint Contract to
    work against) and `_run_prompt_fix_rounds`'s ReviewFixAgent-based loop
    for prompt-based ones (no plan file exists, so GeneratorAgent's
    hardcoded plan-file system prompt would not fit). MR-based review
    files (from `meow gitlab-review`) are rejected up front: that command
    is deliberately read-only and never checks the merge request's code
    out locally, so there is nothing on disk here to fix.
    """
    config = load_config(working_dir)
    describe_lint_plan(config["lint"])

    docs_dir = working_dir / config["docs_dir"]
    resolved_review_file = review_file or _latest_review_file(docs_dir)
    flavor = _detect_review_flavor(resolved_review_file)
    review_text = resolved_review_file.read_text(encoding="utf-8")
    initial_verdict = (_verdict_status(review_text), review_text)

    logger.info(
        "review_fix_review_started",
        review_file=str(resolved_review_file),
        flavor=flavor,
    )

    if flavor == "gitlab":
        raise RuntimeError(
            f"{resolved_review_file} is a GitLab merge request review -- "
            "`meow review-fix-review` can't fix it: there is no local "
            "checkout of the merge request's code, and `gitlab-review` "
            "never creates one. Check out the MR's branch locally and "
            "review-fix-review a plan- or prompt-based review of that "
            "checkout instead, or address the MR feedback directly."
        )

    if flavor == "branch":
        raise RuntimeError(
            f"{resolved_review_file} is a branch review -- `meow "
            "review-fix-review` can't resume it (it doesn't know the "
            "target branch to re-diff against). Run `meow branch-review "
            "<branch> --target <target>` again instead; it already loops "
            "review, fix, and re-review until it passes."
        )

    if flavor == "plan":
        plan_file = resolved_review_file.with_name(
            resolved_review_file.name.removesuffix("-review.md") + ".md"
        )
        if not plan_file.exists():
            raise FileNotFoundError(
                f"{resolved_review_file} looks like a plan review, but its "
                f"plan file {plan_file} no longer exists."
            )
        sprint = build_sprint(working_dir, config)
        passed = await _run_review_rounds(
            sprint, plan_file, initial_verdict=initial_verdict, focus=prompt
        )
    else:
        context = ProjectContext(working_dir, config)
        passed = await _run_prompt_fix_rounds(
            context,
            initial_verdict,
            re_review=lambda: ReviewerAgent(context).review_prompt(prompt),
        )

    if passed:
        logger.info(
            "review_fix_review_complete", review_file=str(resolved_review_file)
        )
        return

    logger.error(
        "review_fix_review_did_not_pass",
        review_file=str(resolved_review_file),
        max_rounds=config["max_rounds"],
    )
    raise RuntimeError(
        f"review-fix-review of {resolved_review_file} did not pass after "
        f"{config['max_rounds']} rounds -- stopping instead of looping "
        "forever. Inspect the review file."
    )
