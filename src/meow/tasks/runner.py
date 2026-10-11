"""Bridge a validated task graph to isolated generator workers."""

import asyncio
from pathlib import Path

from meow.agents.generator import Generator
from meow.execution.run_state import RunStore
from meow.execution.sprint import Sprint, build_sprint
from meow.infrastructure.cancellation import cancel_requested
from meow.infrastructure.test_runner import _argv
from meow.infrastructure.usage import usage_scope
from meow.infrastructure.worktree_setup import run_setup
from meow.project.permissions import PermissionPolicy

from .executor import execute_in_worktrees
from .model import TaskGraph, TaskSpec


async def verify_task(  # ruff: ignore[complex-structure, too-many-statements]
    spec: TaskSpec, worktree: Path
) -> list[dict[str, object]]:
    """Run declared checks before a worker can commit or integrate its slice."""
    evidence: list[dict[str, object]] = []
    for command in spec.verification:
        argv = _argv(command, ())
        if not argv:
            raise ValueError(f"task {spec.id} has an empty verification command")
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=worktree,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                output, _ = await asyncio.wait_for(process.communicate(), 300)
            except TimeoutError:
                process.kill()
                await process.wait()
                raise RuntimeError(
                    f"task {spec.id} verification timed out: {command}"
                ) from None
            except asyncio.CancelledError:
                process.kill()
                await process.wait()
                raise
        except OSError as exc:
            raise RuntimeError(
                f"task {spec.id} verification could not start: {command}: {exc}"
            ) from exc
        item: dict[str, object] = {
            "command": command,
            "exit_code": process.returncode,
            "output": output.decode("utf-8", errors="replace")[:4000],
        }
        evidence.append(item)
        if process.returncode:
            raise RuntimeError(
                f"task {spec.id} verification failed: {command} "
                f"(exit {process.returncode})"
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
    task_state = {
        spec.id: {"status": "waiting", "owned_paths": list(spec.owned_paths)}
        for spec in graph.tasks
    }
    store.transition(run_id, "tasks_waiting", results={"tasks": task_state})
    config = dict(sprint.config)
    config.pop("_run_journal", None)

    def on_change(task_id: str, state: str) -> None:
        task_state[task_id]["status"] = state
        store.transition(run_id, "tasks_running", results={"tasks": task_state})

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
    )
    if not result.passed:
        reason = result.conflict or "one or more tasks failed or were cancelled"
        store.transition(run_id, "tasks_failed", last_failure=reason)
        raise RuntimeError(f"Parallel task execution stopped: {reason}")
    store.transition(
        run_id, "tasks_integrated", results={"integrated_revision": result.revision}
    )
    return True
