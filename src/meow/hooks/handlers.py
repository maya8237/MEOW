"""JSON in/out Claude Code hook handlers."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from meow.infrastructure.lint import run_lint_on_file
from meow.project.config import load_config


def _post_tool_context(text: str) -> dict:
    """The part of a PostToolUse result Claude Code actually shows the model;
    it ignores MEOW's own keys such as `ok` and `failures`."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": text,
        }
    }


def _event(event: Any):
    if not isinstance(event, dict):
        return None, {
            "ok": False,
            "kind": "invalid-event",
            "message": "event must be a JSON object",
        }
    return event, None


def _root(event: dict) -> Path | None:
    value = event.get("working_dir") or event.get("cwd") or event.get("project_dir")
    return Path(value).resolve() if isinstance(value, str) and value else None


def _edited_path(event: dict) -> str | None:
    """The edited file: Claude Code nests it under `tool_input`."""
    tool_input = event.get("tool_input")
    nested = tool_input if isinstance(tool_input, dict) else {}
    value = (
        nested.get("file_path")
        or nested.get("path")
        or event.get("file_path")
        or event.get("path")
    )
    return value if isinstance(value, str) and value else None


def lint_after_edit(event: dict) -> dict:  # ruff: ignore[too-many-return-statements]
    event, error = _event(event)
    if error:
        return error
    if (
        event.get("meow_session") in {"generator", "fixer"}
        or event.get("source") == "meow"
    ):
        return {
            "ok": True,
            "quiet": True,
            "reason": "MEOW SDK lint already handles this edit",
        }
    path, root = _edited_path(event), _root(event)
    if path is None or root is None:
        return {"ok": True, "quiet": True, "reason": "no project edit"}
    if event.get("tool_name") not in {None, "Write", "Edit"}:
        return {"ok": True, "quiet": True, "reason": "not a file edit"}
    try:
        config = load_config(root)
        failures = asyncio.run(
            run_lint_on_file(root, config["lint"], path, config["lint_timeout"])
        )
    except (OSError, ValueError) as exc:
        return {"ok": False, "kind": "diagnostic", "message": str(exc)}
    return _lint_result(path, failures)


def _lint_result(path: str, failures: list[str]) -> dict:
    result = {
        "ok": not failures,
        "quiet": not failures,
        "file": path,
        "failures": failures,
    }
    if failures:
        result |= _post_tool_context(
            f"Lint issues in {path} that could not be auto-fixed:\n"
            + "\n\n".join(failures)
        )
    return result


HANDLERS: dict[str, Callable[[dict], dict]] = {
    "lint_after_edit": lint_after_edit,
}
