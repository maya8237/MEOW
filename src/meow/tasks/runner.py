"""Bridge a validated task graph to isolated generator workers."""

import os
import shlex
from pathlib import Path

from meow.agents.generator import Generator
from meow.execution.run_state import RunStore
from meow.execution.sprint import Sprint, build_sprint
from meow.infrastructure.cancellation import cancel_requested
from meow.infrastructure.checks import run_captured_async
from meow.infrastructure.usage import usage_scope
from meow.infrastructure.worktree_setup import run_setup
from meow.project.config_models import LintCommand
from meow.project.permissions import PermissionPolicy

from .executor import execute_in_worktrees
from .model import TaskGraph, TaskSpec
from .scheduler import PermanentTaskError, TaskOutcome

_SHELL_PUNCTUATION = "&|;<>"


def _verification_argv(command: str) -> list[str]:
    """Plain commands run directly; shell syntax runs in a shell that keeps the
    failing exit status (a pipeline must not report only its last stage)."""
    lexer = shlex.shlex(command, posix=False, punctuation_chars=_SHELL_PUNCTUATION)
    lexer.whitespace_split = True
    lexer.commenters = ""
    operators = [
        token for token in lexer if all(char in _SHELL_PUNCTUATION for char in token)
    ]
    if operators:
        if os.name == "nt":
            if any("|" in token and token != "||" for token in operators):
                raise PermanentTaskError(
                    "task verification cannot check every pipeline exit code on "
                    "Windows; declare separate verification commands instead"
                )
            return ["cmd", "/c", command]
        return ["bash", "-o", "pipefail", "-c", command]
    # Keep the lint launch policy: native Windows programs use their original
    # name, while .cmd/.bat shims need the resolved path.
    return LintCommand(command).argv()


async def verify_task(spec: TaskSpec, worktree: Path) -> list[dict[str, object]]:
    """Run declared checks before a worker can commit or integrate its slice."""
    evidence: list[dict[str, object]] = []
    for command in spec.verification:
        argv = _verification_argv(command)
        if not argv:
            raise ValueError(f"task {spec.id} has an empty verification command")
        try:
            exit_code, output, timed_out = await run_captured_async(
                argv, worktree, os.environ.copy(), 300
            )
            if timed_out:
                raise PermanentTaskError(
                    f"task {spec.id} verification timed out: {command}"
                )
        except OSError as exc:
            raise PermanentTaskError(
                f"task {spec.id} verification could not start: {command}: {exc}"
            ) from exc
        item: dict[str, object] = {
            "command": command,
            "exit_code": exit_code,
            "output": output[:4000],
        }
        evidence.append(item)
        if exit_code != 0:  # None (unknown) is a failure too
            tail = str(item["output"]).strip()[-600:]
            raise PermanentTaskError(
                f"task {spec.id} verification failed: {command} "
                f"(exit {exit_code})" + (f"\n{tail}" if tail else "")
            )
    return evidence


async def run_parallel_plan(  # ruff: ignore[complex-structure, too-many-statements, too-many-arguments, too-many-positional-arguments]
    sprint: Sprint,
    graph: TaskGraph,
    plan_file: Path,
    store: RunStore,
    run_id: str,
) -> bool:
    """Run independent slices, integrate them, then return for final review."""
    saved = store.load(run_id).results.get("tasks")
    saved = saved if isinstance(saved, dict) else {}
    # Tasks already cherry-picked into the integration checkout by an earlier
    # attempt are kept; everything else restarts from a clean worktree.
    integrated = {
        task_id
        for task_id, detail in saved.items()
        if isinstance(detail, dict)
        and detail.get("status") == "complete"
        and task_id in {spec.id for spec in graph.tasks}
    }
    task_state = {
        spec.id: {
            "status": "complete" if spec.id in integrated else "waiting",
            "owned_paths": list(spec.owned_paths),
            **(
                {
                    key: saved[spec.id][key]
                    for key in ("verification", "commit", "worktree", "note")
                    if key in saved[spec.id]
                }
                if spec.id in integrated
                else {}
            ),
        }
        for spec in graph.tasks
    }
    store.transition(run_id, "tasks_waiting", results={"tasks": task_state})
    config = dict(sprint.config)
    config.pop("_run_journal", None)

    def on_change(task_id: str, state: str) -> None:
        task_state[task_id]["status"] = state
        store.transition(run_id, "tasks_running", results={"tasks": task_state})

    def on_outcome(outcome: TaskOutcome) -> None:
        if outcome.error:
            task_state[outcome.id]["error"] = outcome.error[:2000]
        if outcome.worktree:
            task_state[outcome.id]["worktree"] = outcome.worktree
        if outcome.commit:
            task_state[outcome.id]["commit"] = outcome.commit
        if outcome.note:
            task_state[outcome.id]["note"] = outcome.note

    async def prepare(task_dir: Path) -> None:  # ruff: ignore[unused-async]
        manifest = config.get("worktree_setup", {"copy": [], "commands": []})
        policy = config.get("permissions")

        def command_decision(argv: list[str]) -> str | None:
            if not isinstance(policy, PermissionPolicy):
                return None
            return policy.for_role("setup").decision("WorktreeSetup", {"argv": argv})

        run_setup(
            sprint.repo_dir, task_dir, manifest, command_decision=command_decision
        )

    async def implement(spec: TaskSpec, task_dir: Path) -> None:
        task_plan = task_dir / ".meow" / "tasks" / f"{spec.id}.md"
        task_plan.parent.mkdir(parents=True, exist_ok=True)
        task_plan.write_text(
            plan_file.read_text(encoding="utf-8")
            + f"\n\n## Assigned slice: {spec.id}\n"
            + f"Owned paths: {', '.join(spec.owned_paths)}\n",
            encoding="utf-8",
        )
        task_sprint = build_sprint(sprint.repo_dir, config, task_dir, use_worktree=True)
        with usage_scope(store, run_id):
            async with Generator(task_sprint, task_plan) as generator:
                await generator.implement(
                    f"Implement only task {spec.id} from {task_plan}. "
                    f"Change only these owned paths: {', '.join(spec.owned_paths)}. "
                    "Do not implement other tasks or commit."
                )
        evidence = await verify_task(spec, task_dir)
        task_state[spec.id]["verification"] = evidence
        store.transition(run_id, "tasks_running", results={"tasks": task_state})

    result = await execute_in_worktrees(
        sprint.active_working_dir(),
        graph,
        run_id,
        implement,
        max_parallel=2,
        cancel=lambda: cancel_requested(store, run_id),
        on_change=on_change,
        prepare=prepare,
        on_outcome=on_outcome,
        completed=integrated,
    )
    if not result.passed:
        details = [
            f"{item.id}: {item.status}" + (f" ({item.error})" if item.error else "")
            for item in result.outcomes
            if item.status != "complete"
        ]
        reason = result.conflict or "one or more tasks failed or were cancelled"
        if details:
            reason = f"{reason}: " + "; ".join(details)
        store.transition(
            run_id,
            "tasks_failed",
            last_failure=reason,
            results={"tasks": task_state},
        )
        raise RuntimeError(
            f"Parallel task execution stopped: {reason}. Fix the cause and run "
            f"meow resume {run_id} --continue to retry only the unfinished tasks."
        )
    store.transition(
        run_id, "tasks_integrated", results={"integrated_revision": result.revision}
    )
    return True
