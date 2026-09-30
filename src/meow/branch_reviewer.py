"""
meow/branch_reviewer.py

The `meow branch-review` flow: review a local branch's diff against a
target branch entirely locally -- no GitLab MCP, no MR link needed -- then
fix and re-review it until it passes, reusing
`orchestrator._run_prompt_fix_rounds`'s ReviewFixAgent loop the same way
`review_fix_review.py`'s prompt-based half does, except each round's
re-review recomputes the branch's diff against the target instead of the
working tree's diff against HEAD (see `agents.reviewer._branch_diff`).

Unlike `meow issue`, there is no Sprint Contract and nothing gets pushed.
Unlike `meow gitlab-review`, this is never read-only: it fixes what it
finds, either in an isolated worktree (default) or in place on the
caller's current checkout (`--no-worktree`, which requires `branch` to
already be checked out there).
"""

import re
from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.reviewer import BRANCH_REVIEW_FILENAME, ReviewerAgent
from meow.config import load_config
from meow.lint import describe_lint_plan
from meow.logging import get_logger
from meow.orchestrator import _run_prompt_fix_rounds
from meow.worktree import _ensure_existing_branch_worktree, _require_branch_checked_out

logger = get_logger(__name__)

_BRANCH_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize(component: str) -> str:
    return _BRANCH_UNSAFE.sub("-", component).strip("-")


async def run_branch_review(
    working_dir: Path, branch: str, target: str, *, use_worktree: bool = True
) -> None:
    """Review `branch` against `target`, fix findings, and re-review until
    it passes or `max_rounds` is exhausted (then raises `RuntimeError`).
    """
    config = load_config(working_dir)
    describe_lint_plan(config["lint"])

    if use_worktree:
        feature_name = f"branch-review-{_sanitize(branch)}"
        active_dir = _ensure_existing_branch_worktree(working_dir, feature_name, branch)
    else:
        active_dir = working_dir
        _require_branch_checked_out(active_dir, branch)

    context = ProjectContext(active_dir, config, use_worktree=use_worktree)

    logger.info(
        "branch_review_started", branch=branch, target=target, worktree=use_worktree
    )
    initial_verdict = await ReviewerAgent(context).review_branch(target, branch)
    logger.info("branch_review_round_finished", round=1, status=initial_verdict[0])

    passed = await _run_prompt_fix_rounds(
        context,
        initial_verdict,
        re_review=lambda: ReviewerAgent(context).review_branch(target, branch),
    )

    review_file = active_dir / config["docs_dir"] / BRANCH_REVIEW_FILENAME
    if passed:
        logger.info(
            "branch_review_complete", branch=branch, review_file=str(review_file)
        )
        return

    logger.error(
        "branch_review_did_not_pass", branch=branch, max_rounds=config["max_rounds"]
    )
    raise RuntimeError(
        f"branch-review of '{branch}' against '{target}' did not pass after "
        f"{config['max_rounds']} rounds -- stopping instead of looping "
        f"forever. Inspect {review_file}."
    )
