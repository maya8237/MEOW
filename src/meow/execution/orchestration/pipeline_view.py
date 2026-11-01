"""Verify an orchestrator's MR pipeline with the same loop plain runs use.

`verify_pipeline` and `push_fix` talk to a `RunStore`; `OrchestratorPipelineStore`
gives them the same few calls over an orchestrator record, so waiting, pipeline
file fixes, `meow-review` fixes, and `fix_non_meow_jobs` behave identically.
Fixes are pushed to the integration branch while the orchestrator is delivering.
"""

import os
from pathlib import Path
from types import SimpleNamespace

from meow.agents.base import ProjectContext
from meow.agents.fixers import LintFixAgent, ReviewFixAgent
from meow.execution.orchestration.pull_request import origin_url
from meow.execution.orchestration.store import OrchestratorStore
from meow.execution.pipeline_verify import (
    PipelineFixers,
    PipelineOutcome,
    verify_pipeline,
)
from meow.integrations.pipeline_api import PipelineUnavailable, connect
from meow.project.config_schema import PIPELINE_DEFAULTS


class OrchestratorPipelineStore:
    """The `RunStore` calls the pipeline loop makes, over one orchestrator."""

    def __init__(self, store: OrchestratorStore):
        self._store = store
        self.directory = store.directory

    def _path(self, orch_id: str) -> Path:
        # Only the stem matters: lock files hang off it (`.delivery-lock`).
        return self._store.dir_for(orch_id) / "pipeline.json"

    def marker_for(self, orch_id: str) -> Path:
        return self._store.cancel_marker(orch_id)

    def load(self, orch_id: str):
        record = self._store.load(orch_id)
        delivery = dict(record.delivery)
        delivery["branch"] = record.integration["branch"]
        delivery["commit"] = delivery.get("commit") or delivery.get("pushed_commit")
        return SimpleNamespace(
            id=orch_id,
            phase=record.phase,
            worktree=record.integration["worktree"],
            delivery=delivery,
        )

    def transition(self, orch_id: str, phase: str, delivery=None, **_ignored):
        """Merge `delivery`; only a cancellation changes the orchestrator phase."""

        def mutate(record) -> None:
            if delivery is not None:
                record.delivery.update(delivery)
            if phase == "cancelled":
                record.phase = "cancelled"

        self._store.update(orch_id, mutate)


def pipeline_settings(config: dict) -> dict:
    return {**PIPELINE_DEFAULTS, **config.get("pipeline", {})}


def _announce(text: str) -> None:
    print(f"[delivery] {text}", flush=True)


async def verify_orchestrator_mr(
    repo: Path, store: OrchestratorStore, orch_id: str, config: dict
) -> PipelineOutcome:
    """Wait for the MR pipeline and fix it; never raises but for cancellation."""
    view = OrchestratorPipelineStore(store)
    worktree = Path(view.load(orch_id).worktree)

    def context() -> ProjectContext:
        return ProjectContext(worktree, config, use_worktree=True)

    fixers = PipelineFixers(
        lint=lambda: LintFixAgent(context()), review=lambda: ReviewFixAgent(context())
    )
    try:
        provider = connect(origin_url(repo) or "", config, os.environ)
    except PipelineUnavailable as exc:
        outcome = PipelineOutcome("not_verified", 0, "", (), str(exc))
        view.transition(orch_id, "delivering", delivery={"pipeline": _stored(outcome)})
        return outcome
    return await verify_pipeline(
        view, orch_id, provider, fixers, pipeline_settings(config), _announce
    )


def _stored(outcome: PipelineOutcome) -> dict:
    return {
        "status": outcome.status,
        "rounds": outcome.rounds,
        "url": outcome.url,
        "failing": list(outcome.failing),
        "detail": outcome.detail,
    }
