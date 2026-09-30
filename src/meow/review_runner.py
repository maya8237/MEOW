"""
meow/review_runner.py

`meow review`/`meow cr`'s top-level, review-only flows: review the existing
implementation and, for `run_review`, only loop a generator back in if the
review finds something to fix. Split out of `orchestrator.py`, which holds
only the shared generator<->reviewer round-loop engine (`_run_review_rounds`)
this module reuses, the same way `issue_solver.py`/`gitlab_reviewer.py`/
`lint_fix.py` each own their own CLI-facing flow instead of folding it into
the engine module.
"""

from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.reviewer import PROMPT_REVIEW_FILENAME, ReviewerAgent
from meow.config import load_config
from meow.lint import describe_lint_plan
from meow.logging import get_logger
from meow.orchestrator import _run_review_rounds
from meow.plan_files import _latest_plan_file
from meow.sprint import build_sprint

logger = get_logger(__name__)


async def run_review(
    working_dir: Path,
    plan_file: Path | None,
):
    config = load_config(working_dir)
    describe_lint_plan(config["lint"])

    active_dir = working_dir
    if plan_file is not None:
        resolved_plan_file = plan_file
    else:
        resolved_plan_file = _latest_plan_file(active_dir / config["docs_dir"])
    sprint = build_sprint(working_dir, config)

    logger.info(
        "review_started", plan_file=str(resolved_plan_file), working_dir=str(active_dir)
    )
    if await _run_review_rounds(sprint, resolved_plan_file):
        logger.info("review_complete", plan_file=str(resolved_plan_file))
        return

    logger.error(
        "review_did_not_pass",
        plan_file=str(resolved_plan_file),
        max_rounds=config["max_rounds"],
    )
    raise RuntimeError(
        f"Review of {resolved_plan_file} did not pass after "
        f"{config['max_rounds']} rounds -- stopping instead of looping "
        "forever. Inspect the review file."
    )


async def run_prompt_review(
    working_dir: Path,
    prompt: str | None,
):
    """Review the current implementation against a free-text prompt.

    Unlike `run_review`, there is no Sprint Contract task list to loop a
    generator against, so this reports PASS/FAIL rather than gating on it.
    No sprint or plan file is needed either -- a generic `ProjectContext`
    built from config and the working directory is enough for the reviewer.
    """
    config = load_config(working_dir)
    describe_lint_plan(config["lint"])
    active_dir = working_dir
    context = ProjectContext(working_dir, config)

    logger.info("prompt_review_started", working_dir=str(active_dir))
    status, _ = await ReviewerAgent(context).review_prompt(prompt)
    review_file = (
        context.active_working_dir()
        / config["docs_dir"]
        / PROMPT_REVIEW_FILENAME
    )
    logger.info("prompt_review_finished", status=status, review_file=str(review_file))
