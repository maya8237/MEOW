"""Code-owned completion gates for native local reviews."""

import asyncio
import hashlib
import json
from pathlib import Path

from meow.infrastructure.review_verification import (
    review_check_status,
    review_evidence,
    start_review_checks,
)
from meow.project.config import config_root, load_config
from meow.project.plan_files import _detect_review_flavor, plan_review_file


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


def bind_review_checks(  # ruff: ignore[too-many-arguments] -- binds native report scope and plan inputs
    review_file: Path,
    working_dir: Path,
    active_dir: Path,
    *,
    project_scope=False,
    plan_file: Path | None = None,
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
            "review_file": str(review_file.resolve()),
            "plan_file": str(plan_file.resolve()) if plan_file else None,
            "plan_digest": hashlib.sha256(plan_file.read_bytes()).hexdigest()
            if plan_file
            else None,
        },
    )
    return state


def _input_errors(review_file: Path, binding: dict) -> list[str]:
    if binding.get("review_file") != str(review_file.resolve()):
        return ["Review verification binding names a different report; rerun review"]
    if binding.get("plan_file"):
        digest = hashlib.sha256(Path(binding["plan_file"]).read_bytes()).hexdigest()
        if digest != binding.get("plan_digest"):
            return ["Review plan changed after preparation; rerun the review"]
    return []


def review_report_errors(
    review_file: Path,
    repo: Path,
    active_dir: Path,
    *,
    plan_file: Path | None = None,
) -> list[str]:
    """Require a report prepared for this run, its active checkout, and plan."""
    sidecar = review_file.with_suffix(review_file.suffix + ".checks.json")
    try:
        if not review_file.is_file():
            return ["Reviewer produced no report"]
        binding = json.loads(sidecar.read_text(encoding="utf-8"))
        expected_plan = str(plan_file.resolve()) if plan_file else None
        if (
            Path(binding["project_root"]).resolve()
            != config_root(repo, active_dir).resolve()
            or Path(binding["active_dir"]).resolve() != active_dir.resolve()
            or binding.get("plan_file") != expected_plan
            or (
                plan_file is not None
                and review_file.resolve() != plan_review_file(plan_file).resolve()
            )
        ):
            return ["Reviewer report belongs to another run, checkout, or plan"]
        return review_check_errors(review_file)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return ["Reviewer report needs a current native verification binding"]


def review_check_errors(review_file: Path) -> list[str]:  # ruff: ignore[too-many-return-statements, complex-structure] -- fail-closed binding validation
    sidecar = review_file.with_suffix(review_file.suffix + ".checks.json")
    if not sidecar.exists():
        try:
            remote = _detect_review_flavor(review_file) in {"gitlab", "github"}
        except ValueError:
            remote = False
        return (
            []
            if remote
            else [
                "Local review needs a current native verification binding; rerun review"
            ]
        )
    try:
        binding = json.loads(sidecar.read_text(encoding="utf-8"))
        errors = _input_errors(review_file, binding)
        if errors:
            return errors
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
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return [
            "Review verification binding is unreadable or invalid; rerun the review"
        ]
