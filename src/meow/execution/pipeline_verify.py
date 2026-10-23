"""Bounded verify/fix loop for the pipeline of a pushed MEOW run.

Waiting and judging are deterministic; only fixes use agents (`lint_fixer`
for pipeline-file errors, `review_fixer` for failed jobs). Provider errors
end the loop as `not_verified` instead of raising: the code is already pushed.
"""

import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from meow.agents.fixers import LintFixAgent, ReviewFixAgent
from meow.execution.delivery import push_fix
from meow.execution.pipeline_classify import Classification, classify
from meow.execution.run_state import RunStore, _safe
from meow.infrastructure.cancellation import RunCancelled, cancellable, check_cancel
from meow.integrations.ci_review import CiReviewError, _safe_text
from meow.integrations.pipeline_api import (
    PipelineJob,
    PipelineProvider,
    PipelineSnapshot,
    PipelineUnavailable,
)

Status = Literal["verified", "not_verified", "unverified", "no_pipeline", "unvalidated"]
_PROVIDER_ERRORS = (CiReviewError, PipelineUnavailable, OSError, ValueError)
_TERMINAL = frozenset({"success", "failed", "canceled", "skipped"})
_DETAIL_LIMIT = 300


@dataclass(frozen=True)
class PipelineOutcome:
    status: Status
    rounds: int
    url: str
    failing: tuple[str, ...]
    detail: str


@dataclass
class PipelineFixers:
    lint: Callable[[], LintFixAgent]
    review: Callable[[], ReviewFixAgent]


def _one_line(text: str) -> str:
    """First line, redacted and capped, for journal and status text."""
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return str(_safe(_safe_text(first, os.environ, limit=_DETAIL_LIMIT)))


def _in_progress(snapshot: PipelineSnapshot, judged: Classification) -> bool:
    if snapshot.jobs:
        return judged.running
    return snapshot.status not in _TERMINAL


@dataclass
class _Loop:
    store: RunStore
    run_id: str
    provider: PipelineProvider
    fixers: PipelineFixers
    settings: dict
    announce: Callable[[str], None]
    sleep: Callable[[float], Awaitable[object]]
    clock: Callable[[], float]
    stack: AsyncExitStack
    rounds: int = 0
    sha: str = ""
    url: str = ""
    unvalidated: bool = False
    agents: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        delivery = self.store.load(self.run_id).delivery
        pipeline = delivery.get("pipeline")
        if isinstance(pipeline, dict):
            self.rounds = int(pipeline.get("rounds") or 0)
        self.sha = str(delivery.get("commit") or "")

    @property
    def _max(self) -> int:
        return int(self.settings["max_fix_rounds"])

    def _round_text(self) -> str:
        return f"round {min(self.rounds + 1, self._max)} of {self._max}"

    def outcome(
        self, status: Status, failing: tuple[str, ...] = (), detail: str = ""
    ) -> PipelineOutcome:
        return PipelineOutcome(status, self.rounds, self.url, failing, detail)

    def journal(self, phase: str, **fields: object) -> None:
        delivery = self.store.load(self.run_id).delivery
        pipeline = delivery.get("pipeline")
        pipeline = pipeline if isinstance(pipeline, dict) else {}
        pipeline = {**pipeline, "rounds": self.rounds, "url": self.url, **fields}
        self.store.transition(
            self.run_id,
            phase,
            delivery={**delivery, "commit": self.sha, "pipeline": pipeline},
        )

    async def run(self) -> PipelineOutcome:
        if not self.sha:
            return self.outcome("not_verified", detail="no pushed commit recorded")
        while True:
            check_cancel(self.store, self.run_id)
            result = await self._lint_phase()
            if result is None:
                result = await self._wait_phase()
            if isinstance(result, PipelineSnapshot):
                result = await self._judge_phase(result)
            if isinstance(result, PipelineOutcome):
                return result

    async def _lint_phase(self) -> PipelineOutcome | bool | None:
        """`None` when the file is valid or unvalidated, else a fix result."""
        errors = self.provider.lint(Path(self.store.load(self.run_id).worktree))
        self.unvalidated = errors is None
        if not errors:
            return None
        announce = f"Pipeline file invalid; fixing ({self._round_text()})."
        return await self._fix_phase("lint", "\n".join(errors), (), announce)

    async def _wait_phase(self) -> PipelineSnapshot | PipelineOutcome:
        self.announce(f"Pipeline running on {self.sha[:7]} (fix {self._round_text()}).")
        self.journal("pipeline_waiting", status="waiting")
        started = self.clock()
        while True:
            check_cancel(self.store, self.run_id)
            snapshot = self.provider.find_pipeline(self.sha)
            if snapshot is not None:
                self.url = snapshot.url
                judged = classify(snapshot, fix_non_meow=False)
                if not _in_progress(snapshot, judged):
                    return snapshot
            expired = self._expired(snapshot, self.clock() - started)
            if expired is not None:
                return expired
            await self.sleep(float(self.settings["poll_seconds"]))

    def _expired(
        self, snapshot: PipelineSnapshot | None, elapsed: float
    ) -> PipelineOutcome | None:
        if snapshot is None and elapsed >= float(self.settings["start_timeout"]):
            detail = f"no pipeline started for {self.sha[:7]}"
            return self.outcome("no_pipeline", detail=detail)
        if elapsed >= float(self.settings["timeout"]):
            return self.outcome("not_verified", detail="timed out")
        return None

    async def _judge_phase(
        self, snapshot: PipelineSnapshot
    ) -> PipelineOutcome | bool | None:
        judged = classify(snapshot, fix_non_meow=self.settings["fix_non_meow_jobs"])
        unfixed = tuple(
            job.name for job in judged.other_failed if job not in judged.fixable_other
        )
        blocked = self._blocked(snapshot, judged, unfixed)
        if blocked is not None:
            return blocked
        problems = [self._job_problem(job) for job in judged.fixable_other]
        if judged.review_failed:
            problems.insert(0, self._review_problem(judged.review))
        if not problems:
            return self._final(unfixed)
        failing = tuple(job.name for job in snapshot.jobs if job.status == "failed")
        announce = (
            f"meow review job failed; fixing ({self._round_text()})."
            if judged.review_failed
            else None
        )
        return await self._fix_phase("review", "\n\n".join(problems), failing, announce)

    def _blocked(
        self, snapshot: PipelineSnapshot, judged: Classification, unfixed: tuple
    ) -> PipelineOutcome | None:
        """Outcomes that end the loop without any fix."""
        if not snapshot.jobs:
            return self.outcome("not_verified", detail="pipeline has no jobs")
        review = judged.review
        if review is not None and review.status in {"canceled", "skipped"}:
            detail = f"meow review job {review.status}"
            return self.outcome("not_verified", unfixed, detail)
        if judged.review_failed:
            return self._unverified(review, unfixed)
        return None

    def _unverified(
        self, review: PipelineJob, unfixed: tuple
    ) -> PipelineOutcome | None:
        text = self.provider.review_artifact(review, "verdict.json")
        try:
            verdict = json.loads(text) if text else None
        except ValueError:
            return None
        if not isinstance(verdict, dict) or verdict.get("verdict") != "UNVERIFIED":
            return None
        reason = str(verdict.get("failure_reason") or "meow review could not verify")
        return self.outcome("unverified", unfixed, _one_line(reason))

    def _review_problem(self, review: PipelineJob) -> str:
        feedback = self.provider.review_artifact(review, "review.md")
        feedback = feedback or self.provider.job_log(review)
        return f"meow review job `{review.name}` failed ({review.url}):\n{feedback}"

    def _job_problem(self, job: PipelineJob) -> str:
        log = self.provider.job_log(job)
        return f"Pipeline job `{job.name}` failed ({job.url}). Log:\n{log}"

    def _final(self, unfixed: tuple[str, ...]) -> PipelineOutcome:
        if unfixed:
            detail = "failing jobs not fixed: " + ", ".join(unfixed)
            return self.outcome("not_verified", unfixed, detail)
        return self.outcome("unvalidated" if self.unvalidated else "verified")

    async def _fix_phase(
        self, kind: str, problems: str, failing: tuple, announce: str | None
    ) -> PipelineOutcome | bool:
        """Fix, push, and count a round; `True` means poll the new sha."""
        if self.rounds >= self._max:
            return self.outcome("not_verified", failing, "fix rounds exhausted")
        if announce:
            self.announce(announce)
        self.journal("pipeline_fixing", status="fixing")
        agent = await self._agent(kind)
        await cancellable(self.store, self.run_id, agent.fix(problems))
        message = f"MEOW run {self.run_id}: pipeline fix round {self.rounds + 1}"
        try:
            sha = push_fix(self.store, self.run_id, message)
        except RunCancelled:
            raise
        except RuntimeError as exc:
            detail = _one_line(f"fix push failed: {exc}")
            return self.outcome("not_verified", failing, detail)
        if sha is None:
            return self.outcome("not_verified", failing, "fix produced no changes")
        self.rounds, self.sha = self.rounds + 1, sha
        self.journal("pipeline_fixing", status="fixed")
        return True

    async def _agent(self, kind: str) -> LintFixAgent | ReviewFixAgent:
        """One session per fixer across rounds, closed when the loop ends."""
        if kind not in self.agents:
            factory = getattr(self.fixers, kind)
            self.agents[kind] = await self.stack.enter_async_context(factory())
        return self.agents[kind]


async def verify_pipeline(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- one value per collaborator
    store: RunStore,
    run_id: str,
    provider: PipelineProvider,
    fixers: PipelineFixers,
    settings: dict,
    announce: Callable[[str], None],
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> PipelineOutcome:
    """Verify the pushed run's pipeline, fixing up to `max_fix_rounds` times.

    `delivery["pipeline"]["rounds"]` is read first, so a resume keeps the
    count; the final outcome is journaled there too.
    """
    async with AsyncExitStack() as stack:
        loop = _Loop(
            store, run_id, provider, fixers, settings, announce, sleep, clock, stack
        )
        try:
            outcome = await loop.run()
        except _PROVIDER_ERRORS as exc:
            detail = _one_line(str(exc)) or type(exc).__name__
            outcome = loop.outcome("not_verified", detail=detail)
    loop.journal(
        store.load(run_id).phase,
        status=outcome.status,
        failing=list(outcome.failing),
        detail=outcome.detail,
    )
    return outcome
