"""JSON in/out Claude Code hook handlers."""

import asyncio
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from meow.infrastructure.lint import run_lint_on_file
from meow.project.config import load_config
from meow.project.plan_state import PlanStore


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


def _plan_path(event: dict, root: Path) -> Path | None:
    value = event.get("plan_file") or event.get("plan")
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    path = path if path.is_absolute() else root / path
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


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
    return {
        "ok": not failures,
        "quiet": not failures,
        "file": path,
        "failures": failures,
    }


def shaping_ripple(event: dict) -> dict:
    event, error = _event(event)
    if error:
        return error
    path = _edited_path(event) or ""
    if not any(
        token in path.lower() for token in ("shape", "breadboard", "requirements")
    ):
        return {"ok": True, "quiet": True}
    return {
        "ok": True,
        "advisory": True,
        "message": (
            "Shaping artifact changed; review affected places, affordances, and wiring."
        ),
    }


def capture_completed_plan(event: dict) -> dict:  # ruff: ignore[too-many-return-statements]
    event, error = _event(event)
    if error:
        return error
    root = _root(event)
    if root is None:
        return {"ok": True, "quiet": True, "reason": "no project plan event"}
    path = _plan_path(event, root)
    if path is None:
        return {"ok": True, "quiet": True, "reason": "no canonical plan event"}
    if not path.is_file():
        return {
            "ok": False,
            "kind": "diagnostic",
            "message": f"plan does not exist: {path}",
        }
    try:
        store = PlanStore(root)
        current = store.read(path)
        metadata = (
            current
            if current.lifecycle != "draft"
            else store.transition(
                path,
                "draft",
                event.get("run_id"),
                note="Captured by Claude Code plan event",
            )
        )
        return {"ok": True, "plan": str(path), "lifecycle": metadata.lifecycle}
    except (OSError, ValueError) as exc:
        return {"ok": False, "kind": "diagnostic", "message": str(exc)}


def validate_plan_stop(event: dict) -> dict:  # ruff: ignore[too-many-return-statements]
    event, error = _event(event)
    if error:
        return error
    root = _root(event)
    if root is None:
        return {"ok": True, "quiet": True}
    path = _plan_path(event, root)
    if path is None:
        return {"ok": True, "quiet": True}
    try:
        metadata = PlanStore(root).read(path)
    except (OSError, ValueError) as exc:
        return {"ok": False, "kind": "diagnostic", "message": str(exc)}
    if metadata.lifecycle != "complete":
        return {
            "ok": True,
            "advisory": True,
            "message": (
                f"Plan lifecycle is {metadata.lifecycle}; complete or "
                "intentionally leave it in progress."
            ),
        }
    return {"ok": True, "quiet": True, "lifecycle": metadata.lifecycle}


HANDLERS: dict[str, Callable[[dict], dict]] = {
    "lint_after_edit": lint_after_edit,
    "shaping_ripple": shaping_ripple,
    "capture_completed_plan": capture_completed_plan,
    "validate_plan_stop": validate_plan_stop,
}


def main(name: str) -> int:
    try:
        result = HANDLERS[name](json.load(os.fdopen(0, encoding="utf-8")))
    except (json.JSONDecodeError, OSError, KeyError) as exc:
        result = {"ok": False, "kind": "invalid-event", "message": str(exc)}
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok", False) else 1
