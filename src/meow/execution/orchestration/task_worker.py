"""Child-process entry that runs one orchestrator task attempt as a sprint."""

import argparse
from pathlib import Path

from meow.execution.run_state import RunStore
from meow.execution.sprint_runner import run_sprint


async def run_task_attempt(repo: Path, run_id: str, args: argparse.Namespace) -> None:
    """Run the task's sprint unattended in its worktree, committing but never pushing.

    The run record (created by the orchestrator before launch) supplies the
    request and the task worktree; `args` is the parsed `_orchestrator-task`
    invocation.
    """
    record = RunStore(repo).load(run_id)
    await run_sprint(
        Path(record.worktree),
        f"orch-{args.orchestrator_id}-{args.task_id}",
        record.request,
        run_id=run_id,
        record_root=repo,
        source="orchestrator",
        use_worktree=False,
        unattended=True,
        push=False,
        test=args.test,
        resume_at=args.resume_at,
        plan_file=Path(args.plan) if args.plan else None,
        worktree_preexisting=args.preexisting,
    )
