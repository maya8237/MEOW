"""Cleanup command shared by explicit and bulk requests."""

import json

from meow.execution.cleanup import cleanup_run
from meow.execution.orchestration.cleanup import consolidate_orchestrator
from meow.execution.orchestration.model import is_orchestrator_id
from meow.execution.orchestration.store import OrchestratorStateError, OrchestratorStore
from meow.execution.run_state import RunStateError, RunStore


def cleanup_command(repo, run_id=None):
    ids = (
        [run_id]
        if run_id
        else [
            *(path.stem for path in RunStore(repo).record_paths()),
            *(record.id for record in OrchestratorStore(repo).records()),
        ]
    )
    for identity in ids:
        try:
            operation = (
                consolidate_orchestrator
                if is_orchestrator_id(identity)
                else cleanup_run
            )
            report = operation(repo, identity)
        except (OSError, RuntimeError, RunStateError, OrchestratorStateError) as exc:
            report = {"run_id": identity, "removed": [], "kept": [str(exc)]}
        print(json.dumps(report))
    if not ids:
        print("No saved runs to clean up.")
