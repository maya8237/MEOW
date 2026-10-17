"""The orchestrator integration branch: merge tasks, roll back, and push."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from meow.execution.delivery import _FALLBACK_IDENTITY, _NO_IDENTITY, _git
from meow.execution.orchestration.model import (
    OrchestratorRecord,
    OrchestratorTask,
    integration_branch,
    integration_worktree_name,
)
from meow.execution.orchestration.store import OrchestratorStore
from meow.execution.run_state import CheckResult
from meow.infrastructure.checks import configured_checks, run_final_checks
from meow.infrastructure.worktree import _ensure_branch_worktree

_MARKER = re.compile(r"^(<<<<<<<|=======|>>>>>>>)( |$)", re.MULTILINE)


@dataclass(frozen=True)
class MergeOutcome:
    status: Literal["merged", "conflict", "checks_failed", "noop"]
    commit: str | None = None
    conflicted_paths: tuple[str, ...] = ()
    log: Path | None = None


def merge_message(task: OrchestratorTask) -> str:
    """The merge commit subject: names the task and its Jira key, if any."""
    suffix = f" ({task.source_ref})" if task.source_ref else ""
    return f"Merge orchestrator task {task.id}{suffix}"


def has_conflict_markers(path: Path) -> bool:
    """True when the file still holds a git conflict marker line."""
    if not path.is_file():
        return False
    return (
        _MARKER.search(path.read_text(encoding="utf-8", errors="replace")) is not None
    )


def ensure_integration_worktree(
    repo: Path, record: OrchestratorRecord, prefix: str
) -> Path:
    """Create or reuse `.worktrees/orch-<id>` on the integration branch.

    The branch starts at `record.base_branch`. The caller saves the record.
    """
    branch = integration_branch(prefix, record.id)
    path = _ensure_branch_worktree(
        repo,
        integration_worktree_name(record.id),
        branch,
        start_point=record.base_branch,
    )
    record.integration["branch"] = branch
    record.integration["worktree"] = str(path)
    return path


def _worktree(record: OrchestratorRecord) -> Path:
    value = record.integration.get("worktree")
    if not value:
        raise RuntimeError("Integration worktree is not set up")
    return Path(value)


def _head(worktree: Path) -> str:
    return _git(worktree, "rev-parse", "HEAD").stdout.strip()


def _git_committing(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    """Run a git command that creates a commit; retry as MEOW without an identity."""
    result = _git(worktree, *args, check=False)
    if result.returncode and _NO_IDENTITY.search(result.stderr):
        result = _git(worktree, *_FALLBACK_IDENTITY, *args, check=False)
    return result


def _detail(result: subprocess.CompletedProcess) -> str:
    return result.stderr.strip() or result.stdout.strip()


def _unmerged_paths(worktree: Path) -> tuple[str, ...]:
    raw = _git(worktree, "diff", "--name-only", "-z", "--diff-filter=U").stdout
    return tuple(sorted(path for path in raw.split("\0") if path))


def _merge_in_progress(worktree: Path) -> bool:
    return (
        _git(
            worktree, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False
        ).returncode
        == 0
    )


def _log_numbers(directory: Path, task_id: str) -> list[int]:
    pattern = re.compile(rf"integration-{re.escape(task_id)}-(\d+)\.log")
    matches = (
        pattern.fullmatch(path.name) for path in directory.glob("integration-*.log")
    )
    return sorted(int(match.group(1)) for match in matches if match)


def _new_log(directory: Path, task_id: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    number = max(_log_numbers(directory, task_id), default=0) + 1
    return directory / f"integration-{task_id}-{number}.log"


def _latest_log(directory: Path, task_id: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    number = max(_log_numbers(directory, task_id), default=1)
    return directory / f"integration-{task_id}-{number}.log"


def _mark_merging(record: OrchestratorRecord, task_id: str, before: str) -> None:
    record.integration["pre_merge_commit"] = before
    record.integration["merging"] = task_id


def _mark_merged(record: OrchestratorRecord, task_id: str) -> None:
    record.task(task_id).status = "merged"
    if task_id not in record.integration["merged"]:
        record.integration["merged"].append(task_id)
    record.integration["merging"] = None


def _clear_merging(record: OrchestratorRecord) -> None:
    record.integration["merging"] = None


def _require_mergeable(record: OrchestratorRecord, task: OrchestratorTask) -> None:
    if record.integration["merging"]:
        raise RuntimeError(
            f"Merge of task {record.integration['merging']} is still open"
        )
    if task.status != "passed":
        raise ValueError(f"Task {task.id} is {task.status}, not passed")
    if not task.branch:
        raise ValueError(f"Task {task.id} has no branch")


def begin_merge(
    repo: Path, store: OrchestratorStore, orch_id: str, task_id: str
) -> MergeOutcome:
    """Start merging a passed task; the pre-merge commit is saved first."""
    record = store.load(orch_id)
    task = record.task(task_id)
    _require_mergeable(record, task)
    worktree = _worktree(record)
    before = _head(worktree)
    store.update(orch_id, lambda current: _mark_merging(current, task_id, before))
    log = _new_log(store.dir_for(orch_id), task_id)
    ancestor = _git(
        worktree, "merge-base", "--is-ancestor", task.branch, "HEAD", check=False
    )
    if ancestor.returncode == 0:
        log.write_text(
            f"{task.branch} is already merged; nothing to do\n", encoding="utf-8"
        )
        return MergeOutcome("noop", before, log=log)
    merged = _git_committing(
        worktree, "merge", "--no-ff", "-m", merge_message(task), task.branch
    )
    log.write_text(f"{merged.stdout}\n{merged.stderr}\n", encoding="utf-8")
    if merged.returncode == 0:
        return MergeOutcome("merged", _head(worktree), log=log)
    paths = _unmerged_paths(worktree)
    if paths:
        return MergeOutcome("conflict", conflicted_paths=paths, log=log)
    abort_merge(repo, store, orch_id, task_id)
    raise RuntimeError(f"git merge failed: {_detail(merged)}")


def _commit_resolution(worktree: Path, task: OrchestratorTask) -> tuple[str, ...]:
    """Commit a resolved merge; return paths that still hold markers instead."""
    paths = _unmerged_paths(worktree)
    leftover = tuple(path for path in paths if has_conflict_markers(worktree / path))
    if leftover:
        return leftover
    if paths:
        _git(worktree, "add", "-A", "--", *paths)
    committed = _git_committing(worktree, "commit", "-m", merge_message(task))
    if committed.returncode:
        raise RuntimeError(f"git commit failed: {_detail(committed)}")
    return ()


async def _failing_checks(worktree: Path, config: dict) -> list[CheckResult]:
    if not configured_checks(config):
        return []
    results = await run_final_checks(worktree, config)
    return [result for result in results if result.required and not result.passed]


def _append_log(log: Path, failing: list[CheckResult]) -> None:
    lines = [
        f"post-merge check failed: {result.kind} {result.command} "
        f"(exit {result.exit_code})\n{result.output}"
        for result in failing
    ]
    with log.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


async def finish_merge(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
    repo: Path, store: OrchestratorStore, orch_id: str, task_id: str, config: dict
) -> MergeOutcome:
    """Commit a resolved merge, run post-merge checks, and record the task merged."""
    record = store.load(orch_id)
    if record.integration["merging"] != task_id:
        raise RuntimeError(f"No open merge for task {task_id}")
    worktree = _worktree(record)
    log = _latest_log(store.dir_for(orch_id), task_id)
    pending = _merge_in_progress(worktree)
    leftover = _commit_resolution(worktree, record.task(task_id)) if pending else ()
    if leftover:
        return MergeOutcome("conflict", conflicted_paths=leftover, log=log)
    head = _head(worktree)
    changed = head != record.integration["pre_merge_commit"]
    failing = await _failing_checks(worktree, config) if changed else []
    if failing:
        _append_log(log, failing)
        abort_merge(repo, store, orch_id, task_id)
        return MergeOutcome("checks_failed", log=log)
    store.update(orch_id, lambda current: _mark_merged(current, task_id))
    return MergeOutcome("merged" if changed else "noop", head, log=log)


def abort_merge(
    repo: Path, store: OrchestratorStore, orch_id: str, task_id: str
) -> None:
    """Roll the integration worktree back to the recorded pre-merge commit."""
    record = store.load(orch_id)
    open_task = record.integration["merging"]
    if open_task is None:
        return
    if open_task != task_id:
        raise RuntimeError(f"The open merge is for task {open_task}, not {task_id}")
    worktree = _worktree(record)
    _git(worktree, "merge", "--abort", check=False)
    before = record.integration["pre_merge_commit"]
    if before:
        _git(worktree, "reset", "--hard", before)
    store.update(orch_id, _clear_merging)


def reset_mid_merge(repo: Path, store: OrchestratorStore, orch_id: str) -> str | None:
    """Undo a merge a dead driver left open; the task becomes `passed` again."""
    task_id = store.load(orch_id).integration["merging"]
    if task_id is None:
        return None
    abort_merge(repo, store, orch_id, task_id)
    store.update(
        orch_id, lambda current: setattr(current.task(task_id), "status", "passed")
    )
    return task_id


def push_integration(repo: Path, store: OrchestratorStore, orch_id: str) -> str:
    """Push the integration branch unless origin already has this commit."""
    record = store.load(orch_id)
    from meow.execution.orchestration.review_evidence import require_delivery_review
    from meow.project.config import load_config

    if store.cancel_marker(orch_id).exists():
        raise RuntimeError("Orchestrator cancelled before delivery")
    require_delivery_review(repo, record, load_config(repo))
    worktree = _worktree(record)
    branch = record.integration["branch"]
    if "origin" not in _git(worktree, "remote").stdout.splitlines():
        raise RuntimeError("No origin remote configured for automatic push")
    head = _head(worktree)
    listed = _git(
        worktree, "ls-remote", "origin", f"refs/heads/{branch}"
    ).stdout.split()
    if not listed or listed[0] != head:
        _git(worktree, "push", "-u", "origin", branch)
    store.update(orch_id, lambda current: current.delivery.update(pushed_commit=head))
    return head
