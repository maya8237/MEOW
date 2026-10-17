"""Bind an objective review to its code, configuration, and required checks."""

import hashlib
import os
import subprocess
from dataclasses import asdict, replace
from pathlib import Path

from meow.execution.run_state import CheckResult
from meow.infrastructure.checks import (
    _code_paths,
    _reviewed_path,
    checks_current,
    code_revision,
    config_fingerprint,
    configured_checks,
)
from meow.project.plan_files import is_review_report


def capture(repo: Path, worktree: Path, config: dict, results: list) -> dict:
    raw_revision = code_revision(worktree)
    revision = _revision(worktree, config)
    return {
        "revision": revision,
        "config_fingerprint": _fingerprint(repo),
        "checks": [
            asdict(replace(result, revision=revision))
            if result.revision == raw_revision
            else asdict(result)
            for result in results
        ],
    }


def _revision(worktree: Path, config: dict) -> str:
    return code_revision(
        worktree,
        paths=tuple(
            path
            for path in _code_paths(worktree)
            if not is_review_report(
                path, worktree / config.get("docs_dir", ".meow/plans")
            )
        ),
    )


def _fingerprint(repo: Path) -> str:
    try:
        files = config_fingerprint(repo)
    except FileNotFoundError:
        files = "defaults"
    return files


def current(  # ruff: ignore[too-many-arguments] -- check-free validation permits recording a failed round
    repo: Path, worktree: Path, config: dict, evidence: dict, *, verify_checks=True
) -> bool:
    revision = _revision(worktree, config)
    checks = [check for check in configured_checks(config) if check.required]
    return (
        evidence.get("revision") == revision
        and evidence.get("config_fingerprint") == _fingerprint(repo)
        and (
            not verify_checks
            or not checks
            or checks_current(
                _check_results(evidence),
                checks,
                worktree,
                revision,
            )
        )
    )


def _check_results(evidence: dict) -> list[CheckResult]:
    try:
        return [CheckResult(**item) for item in evidence.get("checks", [])]
    except (TypeError, ValueError):
        return []


def report_digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def require_delivery_review(repo: Path, record, config: dict) -> None:
    review = record.review
    path = Path(review["review_file"]) if review.get("review_file") else None
    if (
        review.get("verdict") != "PASS"
        or not current(repo, Path(record.integration["worktree"]), config, review)
        or path is None
        or not review.get("report_digest")
        or report_digest(path) != review["report_digest"]
    ):
        raise RuntimeError(
            "A current passing objective review is required before delivery"
        )
    if not _source_committed(Path(record.integration["worktree"]), config):
        raise RuntimeError(
            "Integration source changes must be committed; run objective review again"
        )


def _source_committed(worktree: Path, config: dict) -> bool:
    commands = (
        ["git", "diff", "HEAD", "--name-only", "-z"],
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
    )
    docs = worktree / config.get("docs_dir", ".meow/plans")
    for command in commands:
        result = subprocess.run(command, cwd=worktree, capture_output=True, check=False)
        if result.returncode:
            return False
        for name in result.stdout.split(b"\0"):
            if name:
                path = Path(os.fsdecode(name))
                if _reviewed_path(path) and not is_review_report(worktree / path, docs):
                    return False
    return True
