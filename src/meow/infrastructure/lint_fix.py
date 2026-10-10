"""The `meow run --lint-fix` flow: run every configured `[[lint]]` command, then
either fix what's left or just report it, depending on how it's invoked.

Standalone CLI use (the default) actually fixes things: an auto-fix pass
runs each command's own fix flag project-wide, then whatever is still
failing is handed to a dedicated fixer agent in a loop, re-checking after
each round, until clean or `max_rounds` is reached.

`--report-only` (used by the `lint-fix` skill wrapper) never fixes or edits
anything and never runs an agent -- it only runs the configured commands in
check-only mode and returns their raw output, because in that mode fixing
what's reported is the calling Claude session's job, not meow's.
"""

from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.fixers import LintFixAgent
from meow.execution.run_state import RunStore
from meow.infrastructure.lint import (
    apply_lint_fixes,
    check_lint_commands,
    describe_lint_plan,
)
from meow.infrastructure.logging import get_logger
from meow.infrastructure.worktree import current_branch
from meow.project.config import load_config
from meow.project.onboarding import onboard_if_needed

logger = get_logger(__name__)


class LintFixError(RuntimeError):
    """`meow run --lint-fix` couldn't get the project's lint clean within max_rounds."""


async def _report_only(working_dir: Path, config: dict) -> str | None:
    problems = await check_lint_commands(
        working_dir, config["lint"], config["lint_timeout"]
    )
    if not problems:
        logger.info("lint_fix_report_clean")
        return None
    logger.info("lint_fix_report_found_problems", count=len(problems))
    return "\n\n".join(problems)


async def _fix_until_clean(working_dir: Path, config: dict) -> None:
    await apply_lint_fixes(working_dir, config["lint"], config["lint_timeout"])
    problems = await check_lint_commands(
        working_dir, config["lint"], config["lint_timeout"]
    )
    if not problems:
        logger.info("lint_fix_clean_after_auto_fix")
        return

    context = ProjectContext(working_dir, config)
    max_rounds = config["max_rounds"]
    async with LintFixAgent(context) as fixer:
        for round_num in range(1, max_rounds + 1):
            logger.info(
                "lint_fix_round_started", round=round_num, problems=len(problems)
            )
            await fixer.fix("\n\n".join(problems))
            problems = await check_lint_commands(
                working_dir, config["lint"], config["lint_timeout"]
            )
            logger.info(
                "lint_fix_round_finished",
                round=round_num,
                status="clean" if not problems else "still_failing",
            )
            if not problems:
                return

    raise LintFixError(
        f"Lint still failing after {max_rounds} rounds -- stopping instead "
        "of looping forever:\n\n" + "\n\n".join(problems)
    )


async def run_lint_fix(working_dir: Path, *, report_only: bool) -> str | None:
    """Run every configured lint command and either report or fix what's left.

    Returns `None` when lint is (or ends up) clean. In report-only mode,
    returns the raw problem text when issues remain -- that's the expected,
    routine outcome there, not a failure, so it's returned rather than
    raised. In standalone mode, unresolved issues raise `LintFixError`
    instead, matching `run_review_command`'s failure convention.
    """
    store = RunStore(working_dir)
    record = store.create(
        source="lint-fix",
        request="",
        repo=working_dir,
        worktree=working_dir,
        branch=current_branch(working_dir),
    )
    try:
        onboard_if_needed(working_dir, working_dir)
        config = load_config(working_dir)
        describe_lint_plan(config["lint"])
        if report_only:
            store.transition(record.id, "checking")
            problems = await _report_only(working_dir, config)
            store.transition(record.id, "complete", results={"lint": problems})
            return problems
        store.transition(record.id, "lint_fix_started")
        await _fix_until_clean(working_dir, config)
        store.transition(record.id, "complete")
        return None
    except BaseException as exc:
        store.transition(record.id, "failed", last_failure=str(exc))
        raise
