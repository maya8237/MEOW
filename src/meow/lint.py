"""
meow/lint.py

The generator's auto-fixing, per-file lint hook: runs every configured
per-file command on each file the generator writes, feeding unfixable
failures back into the generator's context as additional tool-use output.
"""

import asyncio
from pathlib import Path

from meow.config import LintCommand


async def _run_lint_on_file(
    working_dir: Path, commands: list[LintCommand], file_path: str
) -> list[str]:
    """Run every per-file command, returning one report per failure."""
    problems = []
    for entry in commands:
        process = await asyncio.create_subprocess_exec(
            *entry.argv_for_file(file_path),
            cwd=str(working_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            streams = (
                stdout.decode(errors="replace"),
                stderr.decode(errors="replace"),
            )
            report = "\n".join(part for part in streams if part.strip())
            problems.append(f"$ {entry.command}\n{report}".rstrip())
    return problems


def make_lint_hook(working_dir: Path, commands: list[LintCommand]):
    per_file = [entry for entry in commands if entry.per_file]

    async def lint_edited_file(input_data, tool_use_id, context):
        if input_data.get("tool_name") not in {"Write", "Edit"}:
            return {}

        file_path = input_data.get("tool_input", {}).get("file_path")
        if not file_path:
            return {}

        problems = await _run_lint_on_file(working_dir, per_file, file_path)
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
