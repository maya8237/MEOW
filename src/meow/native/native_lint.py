"""
meow/native_lint.py

Lint execution for native (in-Claude-Code-session) execution -- the `lint`
helper `native.py` re-exports for `native_cli.py` to wire to `meow native
lint`. Split out on its own because running the configured lint plan (per
file or project-wide) is a distinct concern from directory bootstrapping
(`native_prepare.py`), round-counter persistence (`native_state.py`), or
prompt construction (`native_prompt.py`).
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from meow.config import LintCommand, load_config
from meow.lint import _run_lint_on_file, apply_lint_fixes, check_lint_commands


@dataclass(frozen=True)
class LintOptions:
    """What `lint` needs beyond the two directories."""

    file_path: str | None = None
    fix: bool = False
    all_blocking: bool = False


async def _lint_one_file(
    active_dir: Path, commands: list[LintCommand], file_path: str, timeout: float
) -> list[str]:
    per_file = [entry for entry in commands if entry.per_file]
    try:
        return await _run_lint_on_file(active_dir, per_file, file_path, timeout)
    except OSError as exc:
        return [f"Could not run lint on {file_path}: {exc}"]


async def _lint_project(
    active_dir: Path,
    commands: list[LintCommand],
    timeout: float,
    *,
    all_blocking: bool,
) -> dict:
    blocking: list[str] = []
    informational: list[str] = []
    for entry in commands:
        try:
            problems = await check_lint_commands(active_dir, [entry], timeout)
        except OSError as exc:
            problems = [f"$ {entry.command}\nCould not run: {exc}"]
        (blocking if all_blocking or entry.gate else informational).extend(problems)
    return {"clean": not blocking, "blocking": blocking, "informational": informational}


def lint(working_dir: Path, active_dir: Path, options: LintOptions) -> dict:
    """Run the configured lint plan in `active_dir`.

    With `options.file_path`, behave like the SDK generator's post-edit hook:
    run each per-file command (with its fix flag) on that file and report
    what could not be auto-fixed. Otherwise run every command project-wide in
    check-only mode, split into blocking (`gate`) and informational
    findings; `options.fix` first applies each command's own fix flag.

    `options.all_blocking` treats every command as blocking regardless of
    `gate`, ignoring the review-only gate/informational split: CLI mode's
    `meow run --lint-fix` fixes/reports every configured command unconditionally
    (`gate` only means "this command's failure fails a sprint review"), so
    the `lint-fix` skill's native mode passes `all_blocking=True` to match
    that CLI behavior instead of silently skipping non-gate commands.
    """
    config = load_config(working_dir)
    commands, timeout = config["lint"], config["lint_timeout"]
    if options.file_path:
        problems = asyncio.run(
            _lint_one_file(active_dir, commands, options.file_path, timeout)
        )
        return {"clean": not problems, "problems": problems}
    if options.fix:
        asyncio.run(apply_lint_fixes(active_dir, commands, timeout))
    return asyncio.run(
        _lint_project(active_dir, commands, timeout, all_blocking=options.all_blocking)
    )
