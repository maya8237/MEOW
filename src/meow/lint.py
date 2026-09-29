"""
meow/lint.py

The generator's auto-fixing, per-file lint hook: runs every configured
per-file command on each file the generator writes, feeding unfixable
failures back into the generator's context as additional tool-use output.
"""

import asyncio
from pathlib import Path

from meow.config import LintCommand
from meow.logging import get_logger

logger = get_logger(__name__)


async def _run_subprocess(
    working_dir: Path, argv: list[str], timeout: float
) -> tuple[int, str] | None:
    """Run one subprocess, returning (returncode, combined output), or None
    on timeout."""
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(working_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        return None

    streams = (stdout.decode(errors="replace"), stderr.decode(errors="replace"))
    return process.returncode, "\n".join(part for part in streams if part.strip())


async def _run_one_lint_command(
    working_dir: Path, entry: LintCommand, file_path: str, timeout: float
) -> str | None:
    """Run one per-file command, returning its failure report, or None."""
    result = await _run_subprocess(working_dir, entry.argv_for_file(file_path), timeout)
    if result is None:
        logger.warning(
            "lint_command_timed_out",
            command=entry.command,
            file=file_path,
            timeout=timeout,
        )
        return f"$ {entry.command}\nTimed out after {timeout}s -- killed."

    returncode, report = result
    if returncode == 0:
        return None

    logger.info(
        "lint_command_failed",
        command=entry.command,
        file=file_path,
        returncode=returncode,
    )
    return f"$ {entry.command}\n{report}".rstrip()


async def apply_lint_fixes(
    working_dir: Path, commands: list[LintCommand], timeout: float
) -> None:
    """Run each configured command's own fix flag project-wide, where set.

    Exit codes aren't checked here -- a linter may still exit non-zero after
    fixing what it can, for issues it cannot auto-fix on its own. Callers
    that need to know what's actually still broken re-check separately with
    `check_lint_commands`.
    """
    for entry in commands:
        if not entry.fix_flag:
            continue
        result = await _run_subprocess(
            working_dir, [*entry.argv(), entry.fix_flag], timeout
        )
        if result is None:
            logger.warning(
                "lint_fix_command_timed_out", command=entry.command, timeout=timeout
            )


async def check_lint_commands(
    working_dir: Path, commands: list[LintCommand], timeout: float
) -> list[str]:
    """Run every configured command project-wide in check-only mode.

    Returns one failure report per command that fails, in the same
    `$ <command>\\n<output>` shape the per-file hook already reports.
    """
    problems = []
    for entry in commands:
        result = await _run_subprocess(working_dir, entry.argv(), timeout)
        if result is None:
            logger.warning(
                "lint_command_timed_out", command=entry.command, timeout=timeout
            )
            problems.append(f"$ {entry.command}\nTimed out after {timeout}s -- killed.")
            continue

        returncode, report = result
        if returncode != 0:
            logger.info(
                "lint_command_failed", command=entry.command, returncode=returncode
            )
            problems.append(f"$ {entry.command}\n{report}".rstrip())
    return problems


async def _run_lint_on_file(
    working_dir: Path,
    commands: list[LintCommand],
    file_path: str,
    timeout: float,
) -> list[str]:
    """Run every per-file command, returning one report per failure."""
    problems = []
    for entry in commands:
        report = await _run_one_lint_command(working_dir, entry, file_path, timeout)
        if report is not None:
            problems.append(report)
    return problems


def describe_lint_plan(commands: list[LintCommand]) -> None:
    """Report the configured lint commands before a sprint spends anything."""
    for entry in commands:
        logger.info(
            "lint_command_configured",
            command=entry.command,
            per_file=entry.per_file,
            gate=entry.gate,
            fix_flag=entry.fix_flag,
        )


def make_lint_hook(
    working_dir: Path, commands: list[LintCommand], timeout: float = 60
):
    per_file = [entry for entry in commands if entry.per_file]

    async def lint_edited_file(input_data, tool_use_id, context):
        if input_data.get("tool_name") not in {"Write", "Edit"}:
            return {}

        file_path = input_data.get("tool_input", {}).get("file_path")
        if not file_path:
            return {}

        logger.debug("lint_hook_running", file=file_path, commands=len(per_file))
        problems = await _run_lint_on_file(working_dir, per_file, file_path, timeout)
        if not problems:
            return {}  # clean or auto-fixed -- nothing fed back into context

        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    f"Lint issues in {file_path} that could not be "
                    "auto-fixed:\n" + "\n\n".join(problems)
                ),
            }
        }

    return lint_edited_file
