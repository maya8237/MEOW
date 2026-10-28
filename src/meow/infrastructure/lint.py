"""The generator's auto-fixing, per-file lint hook: runs every configured
per-file command on each file the generator writes, feeding unfixable
failures back into the generator's context as additional tool-use output.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from meow.infrastructure.checks import run_captured_async
from meow.infrastructure.logging import get_logger
from meow.infrastructure.python_dependencies import python_environment
from meow.project.config_models import LintCommand, resolve_command_cwd

logger = get_logger(__name__)

_CANNOT_START = 127  # the shell's "command not found" status


@dataclass(frozen=True)
class LintGateEvidence:
    blocking: tuple[str, ...] = ()
    informational: tuple[str, ...] = ()

    @property
    def blocking_failed(self) -> bool:
        return bool(self.blocking)

    def report(self) -> str:
        sections = []
        if self.blocking:
            sections.append("Blocking lint failures:\n" + "\n\n".join(self.blocking))
        if self.informational:
            sections.append(
                "Advisory lint findings:\n" + "\n\n".join(self.informational)
            )
        return (
            "\n\n".join(sections)
            if sections
            else "All configured lint commands passed."
        )


async def _run_subprocess(
    working_dir: Path,
    argv: list[str],
    timeout: float,
    env: dict[str, str] | None = None,
) -> tuple[int, str] | None:
    """Run one subprocess, returning (returncode, combined output), or None
    on timeout. A program that cannot start (not installed, bad shim) is a
    failing command, not a crash, so every caller reports it the same way."""
    try:
        code, output, timed_out = await run_captured_async(
            argv, working_dir, env if env is not None else dict(os.environ), timeout
        )
    except OSError as exc:
        return _CANNOT_START, f"Could not run lint command {argv[0]}: {exc}"
    return None if timed_out else (code, output)


async def _run_one_lint_command(  # ruff: ignore[too-many-return-statements]
    working_dir: Path, entry: LintCommand, file_path: str, timeout: float
) -> str | None:
    """Run one per-file command, returning its failure report, or None."""
    effective_timeout = entry.timeout or timeout
    try:
        cwd = resolve_command_cwd(working_dir, entry.cwd)
        argv = entry.argv_for_file(file_path)
    except ValueError as exc:  # missing cwd, empty or unparsable command
        return f"$ {entry.command}\n{exc}"
    env = python_environment(working_dir, entry.env)
    result = await _run_subprocess(cwd, argv, effective_timeout, env)
    if result is None:
        logger.warning(
            "lint_command_timed_out",
            command=entry.command,
            file=file_path,
            timeout=effective_timeout,
        )
        return f"$ {entry.command}\nTimed out after {effective_timeout}s -- killed."

    returncode, report = result
    if entry.fix_flag:
        # The fixing command may exit successfully while leaving findings.
        # Report the check-only state of the edited file, not stale fix output.
        result = await _run_subprocess(
            cwd, [*entry.argv(), file_path], effective_timeout, env
        )
        if result is None:
            return (
                f"$ {entry.command}\n"
                f"Check after fix timed out after {effective_timeout}s."
            )
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
        try:
            cwd = resolve_command_cwd(working_dir, entry.cwd)
            argv = [*entry.argv(), entry.fix_flag]
        except ValueError as exc:  # the check pass reports it as a failure
            logger.warning(
                "lint_fix_command_skipped", command=entry.command, reason=str(exc)
            )
            continue
        result = await _run_subprocess(
            cwd,
            argv,
            entry.timeout or timeout,
            python_environment(working_dir, entry.env),
        )
        if result is None:
            logger.warning(
                "lint_fix_command_timed_out",
                command=entry.command,
                timeout=entry.timeout or timeout,
            )


async def check_lint_commands(
    working_dir: Path, commands: list[LintCommand], timeout: float
) -> list[str]:
    """Run every configured command project-wide in check-only mode.

    Returns one failure report per command that fails, in the same
    `$ <command>\\n<output>` shape the per-file hook already reports.
    """
    evidence = await check_lint_evidence(working_dir, commands, timeout)
    return [*evidence.blocking, *evidence.informational]


async def check_lint_evidence(
    working_dir: Path, commands: list[LintCommand], timeout: float
) -> LintGateEvidence:
    blocking = []
    informational = []
    for entry in commands:
        effective_timeout = entry.timeout or timeout
        try:
            cwd = resolve_command_cwd(working_dir, entry.cwd)
            argv = entry.argv()
        except ValueError as exc:  # missing cwd, empty or unparsable command
            (blocking if entry.gate else informational).append(
                f"$ {entry.command}\n{exc}"
            )
            continue
        result = await _run_subprocess(
            cwd, argv, effective_timeout, python_environment(working_dir, entry.env)
        )
        if result is None:
            logger.warning(
                "lint_command_timed_out",
                command=entry.command,
                timeout=effective_timeout,
            )
            report = (
                f"$ {entry.command}\nTimed out after {effective_timeout}s -- killed."
            )
            (blocking if entry.gate else informational).append(report)
            continue

        returncode, report = result
        if returncode != 0:
            logger.info(
                "lint_command_failed", command=entry.command, returncode=returncode
            )
            message = f"$ {entry.command}\n{report}".rstrip()
            (blocking if entry.gate else informational).append(message)
    return LintGateEvidence(tuple(blocking), tuple(informational))


async def run_lint_on_file(  # ruff: ignore[complex-structure, too-many-statements]
    working_dir: Path,
    commands: list[LintCommand],
    file_path: str,
    timeout: float,
) -> list[str]:
    """Run every per-file command, returning one report per failure."""
    problems = []
    for entry in commands:
        if not entry.per_file:
            continue
        normalized_file = file_path
        raw_path = Path(file_path.replace("\\", "/"))
        if raw_path.is_absolute():
            try:
                normalized_file = str(
                    raw_path.resolve().relative_to(working_dir.resolve())
                )
            except ValueError:
                continue
        normalized_file = normalized_file.replace("\\", "/")
        if not entry.matches_file(Path(normalized_file)):
            continue
        try:
            relative = Path(normalized_file).relative_to(entry.cwd)
        except ValueError:
            relative = Path(normalized_file)
        report = await _run_one_lint_command(
            working_dir, entry, os.fspath(relative), timeout
        )
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


async def _lint_or_skip(
    working_dir: Path, commands: list[LintCommand], file_path: str, timeout: float
) -> list[str]:
    """Per-file lint problems; a file the OS won't let us inspect (a `WinError`)
    is skipped so the agent's turn goes on."""
    try:
        return await run_lint_on_file(working_dir, commands, file_path, timeout)
    except OSError as exc:
        logger.warning("lint_hook_failed", file=file_path, reason=str(exc))
        return []


MAX_FEEDBACK_CHARS = 4000  # per report: a verbose linter must not flood the turn


def _bounded(report: str) -> str:
    if len(report) <= MAX_FEEDBACK_CHARS:
        return report
    return report[:MAX_FEEDBACK_CHARS].rstrip() + "\n[output truncated]"


def _file_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def make_lint_hook(working_dir: Path, commands: list[LintCommand], timeout: float = 60):
    async def lint_edited_file(input_data, tool_use_id, context):
        file_path = input_data.get("tool_input", {}).get("file_path")
        if input_data.get("tool_name") not in {"Write", "Edit"} or not file_path:
            return {}

        logger.debug("lint_hook_running", file=file_path, commands=len(commands))
        target = Path(file_path.replace("\\", "/"))
        target = target if target.is_absolute() else working_dir / target
        written = _file_bytes(target)
        try:
            problems = await run_lint_on_file(working_dir, commands, file_path, timeout)
        except (
            OSError
        ) as exc:  # a file the OS won't let us inspect must not crash the turn
            logger.warning("lint_hook_failed", file=file_path, reason=str(exc))
            return {}

        # The model's view of the file is stale once a fixer rewrote it.
        auto_fixed = written is not None and _file_bytes(target) != written
        return _hook_feedback(file_path, auto_fixed, problems)

    return lint_edited_file


def _hook_feedback(file_path: str, auto_fixed: bool, problems: list[str]) -> dict:
    notes = []
    if auto_fixed:
        notes.append(
            f"Lint auto-fixed {file_path}; re-read it before editing it again."
        )
    if problems:
        notes.append(
            f"Lint issues in {file_path} that could not be auto-fixed:\n"
            + "\n\n".join(_bounded(problem) for problem in problems)
        )
    if not notes:
        return {}  # clean and unchanged -- nothing fed back into context
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "\n\n".join(notes),
        }
    }
