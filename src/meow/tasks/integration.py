"""Apply isolated worker commits into the final verification checkout."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .scheduler import TaskOutcome

_COMMIT = re.compile(r"[0-9a-f]{40,64}\Z")


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=check
    )


@dataclass(frozen=True)
class IntegrationResult:
    applied: tuple[str, ...]
    revision: str
    conflict: str | None = None


def integrate_results(  # ruff: ignore[complex-structure]
    repo: Path, base: str, outcomes: tuple[TaskOutcome, ...]
) -> IntegrationResult:
    """Cherry-pick successful commits; retain worker checkouts on conflict."""
    repo = repo.resolve()
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    if head != base:
        raise ValueError("integration checkout changed since task planning")
    if _git(repo, "status", "--porcelain").stdout.strip():
        raise ValueError("integration checkout has uncommitted changes")
    for outcome in outcomes:
        if outcome.status != "complete" or not outcome.commit:
            return IntegrationResult((), head, f"{outcome.id} did not complete")
        if not _COMMIT.fullmatch(outcome.commit):
            raise ValueError(f"{outcome.id} has an invalid commit ID")
    applied = []
    for outcome in outcomes:
        result = _git(repo, "cherry-pick", outcome.commit, check=False)
        if result.returncode:
            _git(repo, "cherry-pick", "--abort", check=False)
            return IntegrationResult(
                tuple(applied),
                _git(repo, "rev-parse", "HEAD").stdout.strip(),
                f"{outcome.id} conflicts during integration",
            )
        applied.append(outcome.id)
    return IntegrationResult(
        tuple(applied), _git(repo, "rev-parse", "HEAD").stdout.strip()
    )
