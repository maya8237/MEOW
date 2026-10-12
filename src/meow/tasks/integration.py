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


def _identity(repo: Path) -> list[str]:
    """`-c` overrides giving cherry-pick a committer when git has none (a
    fresh CI container); a configured identity is left alone."""
    if _git(repo, "var", "GIT_COMMITTER_IDENT", check=False).returncode == 0:
        return []
    return ["-c", "user.name=MEOW", "-c", "user.email=meow@local"]


@dataclass(frozen=True)
class IntegrationResult:
    applied: tuple[str, ...]
    revision: str
    conflict: str | None = None


def integrate_results(  # ruff: ignore[complex-structure, too-many-branches]
    repo: Path, base: str, outcomes: tuple[TaskOutcome, ...]
) -> IntegrationResult:
    """Cherry-pick successful commits; retain worker checkouts on conflict."""
    repo = repo.resolve()
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    if head != base:
        # A commit made in the integration checkout mid-run is fine as long as
        # it builds on the revision the tasks started from: cherry-picks then
        # land on top of it. Anything else (reset, branch switch) is refused.
        ancestor = _git(repo, "merge-base", "--is-ancestor", base, head, check=False)
        if ancestor.returncode:
            raise ValueError(
                "integration checkout moved off the task base "
                f"({base[:12]} -> {head[:12]}); restore the branch and resume"
            )
    if _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise ValueError(
            "integration checkout has uncommitted changes; commit or stash them "
            "and run meow resume to integrate the finished tasks"
        )
    for outcome in outcomes:
        if outcome.status != "complete" or not outcome.commit:
            return IntegrationResult((), head, f"{outcome.id} did not complete")
        if not _COMMIT.fullmatch(outcome.commit):
            raise ValueError(f"{outcome.id} has an invalid commit ID")
    applied = []
    identity = _identity(repo)
    for outcome in outcomes:
        result = _git(repo, *identity, "cherry-pick", outcome.commit, check=False)
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
