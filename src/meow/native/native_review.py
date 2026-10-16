"""Code-owned completion gates for native local reviews."""

import asyncio
import json
from pathlib import Path

from meow.infrastructure.review_verification import (
    review_check_status,
    review_evidence,
    start_review_checks,
)
from meow.project.config import config_root, load_config


def review_checks(
    working_dir: Path, active_dir: Path, *, background=False, status=False
) -> dict:
    project = config_root(working_dir, active_dir)
    if background:
        return start_review_checks(project, active_dir)
    config = load_config(project)
    if not status:
        asyncio.run(review_evidence(active_dir, config))
    return review_check_status(active_dir, config)


def bind_review_checks(
    review_file: Path, working_dir: Path, active_dir: Path, *, project_scope=False
) -> dict:
    from meow.ui.sessions import _atomic_json

    project = config_root(working_dir, active_dir)
    state = review_check_status(active_dir, load_config(project))
    _atomic_json(
        review_file.with_suffix(review_file.suffix + ".checks.json"),
        {
            "project_root": str(project.resolve()),
            "active_dir": str(active_dir.resolve()),
            "key": state["key"],
            "project_scope": project_scope,
        },
    )
    return state


def review_check_errors(review_file: Path) -> list[str]:  # ruff: ignore[too-many-return-statements] -- fail-closed binding validation
    sidecar = review_file.with_suffix(review_file.suffix + ".checks.json")
    if not sidecar.exists():
        return []
    try:
        binding = json.loads(sidecar.read_text(encoding="utf-8"))
        if (
            binding.get("project_scope")
            and not review_file.with_suffix(
                review_file.suffix + ".scope.json"
            ).is_file()
        ):
            return ["Required project coverage inventory is missing"]
        state = review_check_status(
            Path(binding["active_dir"]), load_config(Path(binding["project_root"]))
        )
        if state["key"] != binding["key"]:
            return [
                "Review inputs changed after preparation; "
                "rerun the review and required checks"
            ]
        if not state["passed"]:
            return [
                f"Required review verification is {state['status']}",
                *state["blocking"],
            ]
        return []
    except (OSError, ValueError, KeyError, TypeError):
        return [
            "Review verification binding is unreadable or invalid; rerun the review"
        ]
