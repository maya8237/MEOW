"""The whole-objective review/fix loop on the integration branch.

Every task already passed its own gates; this round grades the merged result
against the objective. A reviewer reads the branch, the harness runs the
configured checks (and, with `--test`, the tester), a fixer repairs findings,
and the loop repeats for at most `[orchestrator].review_rounds` reviews.
"""

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path

from meow.agents.base import ProjectContext
from meow.agents.reviewer import force_fail
from meow.execution.delivery import commit_worktree
from meow.execution.orchestration import review_evidence
from meow.execution.orchestration.model import OrchestratorRecord
from meow.execution.orchestration.report import task_label
from meow.execution.orchestration.store import OrchestratorStore
from meow.execution.orchestration.tick import block_task
from meow.execution.run_failures import park_on_check_timeout
from meow.infrastructure.checks import run_final_checks
from meow.infrastructure.test_runner import VerificationSetupError, prepared_test_stage
from meow.project.plan_files import stage_file

_REQUEST_LINE_LIMIT = 200


def objective_text(record: OrchestratorRecord) -> str:
    """The objective plus one line per task: what the reviewer grades against."""
    lines = [record.objective.strip(), "", "Tasks:"]
    for task in record.tasks:
        first = next((s.strip() for s in task.request.splitlines() if s.strip()), "")
        lines.append(f"- {task_label(task)}: {first[:_REQUEST_LINE_LIMIT]}")
    return "\n".join(lines)


def tester_configured(config: dict) -> bool:
    tester = config.get("tester") or {}
    return bool(tester.get("tests") or tester.get("browser"))


def checks_appendix(failed: list) -> str:
    lines = ["Required checks failed after the last change:"]
    for result in failed:
        outcome = "timed out" if result.timed_out else f"exit code {result.exit_code}"
        lines.append(f"- {result.command} ({outcome})")
    return "\n".join(lines)


@dataclass
class _Round:
    status: str
    verdict: str
    review_file: Path | None
    evidence: list[str] = field(default_factory=list)
    binding: dict = field(default_factory=dict)


class _ObjectiveReview:
    def __init__(
        self,
        store: OrchestratorStore,
        orch_id: str,
        config: dict,
        agents,
    ):
        record = store.load(orch_id)
        self.store = store
        self.orch_id = orch_id
        self.config = config
        self.agents = agents
        self.rounds = config["orchestrator"]["review_rounds"]
        self.used_rounds = record.review["round"]
        self.base = record.base_branch
        self.branch = record.integration["branch"]
        self.worktree = Path(record.integration["worktree"])
        self.context = ProjectContext(self.worktree, config, use_worktree=True)
        self.objective = objective_text(record)
        self.test = bool(record.options.get("test")) and tester_configured(config)

    async def run(self) -> str:
        self.store.update(self.orch_id, lambda r: setattr(r, "phase", "reviewing"))
        fixer = None
        outcome = _Round("FAIL", "Objective review rounds are exhausted", None)
        async with AsyncExitStack() as stack:
            for number in range(self.used_rounds + 1, self.rounds + 1):
                outcome = await self._round()
                if self.store.cancel_marker(self.orch_id).exists():
                    self.store.update(
                        self.orch_id, lambda r: setattr(r, "phase", "cancelled")
                    )
                    return "CANCELLED"
                self._record(number, outcome)
                if outcome.status == "PASS":
                    return "PASS"
                if number < self.rounds:
                    if fixer is None:
                        fixer = await stack.enter_async_context(
                            self.agents.fixer(self.context)
                        )
                    await self._fix(fixer, outcome, number)
        self._block(outcome)
        return "FAIL"

    async def _round(self) -> _Round:
        results = await run_final_checks(self.worktree, self.config)
        binding = review_evidence.capture(
            self.store.repo, self.worktree, self.config, results
        )
        park_on_check_timeout(results)
        failed = [r for r in results if r.required and not r.passed]
        reviewer = self.agents.reviewer(self.context)
        status, verdict = await reviewer.review_branch(
            self.base, self.branch, objective=self.objective
        )
        review_file = getattr(reviewer, "last_review_file", None)
        if failed:
            status, verdict = "FAIL", force_fail(verdict, checks_appendix(failed))
            if review_file is not None:
                review_file.write_text(verdict, encoding="utf-8")
        outcome = _Round(status, verdict, review_file)
        if review_file is not None:
            outcome.evidence.append(str(review_file))
        if status == "PASS" and self.test:
            await self._test(outcome)
        if outcome.status == "PASS" and not review_evidence.current(
            self.store.repo, self.worktree, self.config, binding
        ):
            outcome.status = "FAIL"
            outcome.verdict = force_fail(
                outcome.verdict,
                "Objective review evidence became stale; review current code again.",
            )
        outcome.binding = binding
        return outcome

    async def _test(self, outcome: _Round) -> None:
        try:
            async with prepared_test_stage(self.worktree, self.config) as evidence:
                tester = self.agents.tester(self.context)
                status, verdict = await tester.test_request(self.objective, evidence)
        except VerificationSetupError as exc:
            outcome.status = "FAIL"
            outcome.verdict = f"Tester feedback:\nTester setup failed: {exc}"
            return
        if status != "PASS" or evidence.blocking_failed:
            outcome.status = "FAIL"
            outcome.verdict = f"Tester feedback:\n{verdict}"
            report = stage_file(self.worktree / self.config["docs_dir"], "tester")
            if report.exists():
                outcome.evidence.append(str(report))

    async def _fix(self, fixer, outcome: _Round, number: int) -> None:
        await fixer.fix(outcome.verdict)
        message = ["-m", f"Orchestrator review fixes (round {number})"]
        await asyncio.to_thread(commit_worktree, self.worktree, message)

    def _record(self, number: int, outcome: _Round) -> None:
        review = {
            "round": number,
            "verdict": outcome.status,
            "review_file": str(outcome.review_file) if outcome.review_file else None,
            **outcome.binding,
            "report_digest": review_evidence.report_digest(outcome.review_file)
            if outcome.review_file
            else None,
        }
        self.store.update(self.orch_id, lambda r: setattr(r, "review", review))

    def _block(self, outcome: _Round) -> None:
        blocker = {
            "task_id": None,
            "cause": "objective-review",
            "evidence": outcome.evidence,
            "needed": (
                f"The objective review still failed after {self.rounds} "
                f"round(s). Fix the findings in the integration worktree "
                f"{self.worktree} and commit them there, or narrow the "
                "objective, then resume."
            ),
        }
        self.store.update(self.orch_id, lambda r: block_task(r, blocker))


async def run_objective_review(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- the pinned review signature
    repo: Path, store: OrchestratorStore, orch_id: str, config: dict, agents
) -> str:
    """Review (and fix) the integration branch; return `PASS` or `FAIL`.

    A final FAIL blocks the orchestrator with cause `objective-review`.
    """
    return await _ObjectiveReview(store, orch_id, config, agents).run()
