"""Read-only reports over durable MEOW run journals."""

from dataclasses import dataclass, field
from pathlib import Path

from meow.execution.run_state import RunRecord, RunStateError, RunStore


@dataclass(frozen=True)
class EvaluationReport:
    run_id: str
    verdict: str
    observed: dict[str, object] = field(default_factory=dict)
    judgments: dict[str, object] = field(default_factory=dict)
    evidence: tuple[str, ...] = ()
    unavailable: tuple[str, ...] = ()
    comparison: dict[str, object] | None = None

    def render(self, verbose: bool = False) -> str:
        lines = [f"Run {self.run_id}: {self.verdict}"]
        for key, value in self.observed.items():
            lines.append(f"Observed {key}: {value}")
        if verbose:
            for key, value in self.judgments.items():
                lines.append(f"Judgment {key}: {value}")
        for item in self.unavailable:
            lines.append(f"Unavailable: {item}")
        if self.evidence:
            lines.append("Evidence: " + ", ".join(self.evidence))
        if self.comparison is not None:
            lines.append(f"Comparison: {self.comparison}")
        return "\n".join(lines)


def _record_evidence(record: RunRecord) -> tuple[str, ...]:
    result = []
    if record.plan_file:
        result.append(record.plan_file)
    if record.review_file:
        result.append(record.review_file)
    result.extend(f"transition:{item.get('phase')}" for item in record.transitions)
    return tuple(result)


def _load(store: RunStore, run_id: str | None) -> RunRecord:
    return store.latest() if run_id is None else store.load(run_id)


def evaluate_run(
    working_dir: Path, run_id: str | None = None, compare_ids: tuple[str, ...] = ()
) -> EvaluationReport:
    """Evaluate a run without agents, subprocesses, writes, or verdict changes."""
    store = RunStore(working_dir)
    record = _load(store, run_id)
    observed: dict[str, object] = {
        "phase": record.phase,
        "round": record.round,
        "delivery": record.delivery or "unavailable",
    }
    unavailable: list[str] = []
    if record.usage in (None, "unavailable", {}):
        unavailable.append("SDK usage/cost was not recorded")
    results = record.results or {}
    if "tester" in results:
        observed["tester"] = results["tester"]
    else:
        unavailable.append("tester evidence")
    if "browser" in results:
        observed["browser"] = results["browser"]
    else:
        unavailable.append("browser evidence")
    if not record.plan_file:
        unavailable.append("plan reference")
    judgments = {
        "correctness": "observed from stored verdict and gates",
        "plan_adherence": "available when plan and review evidence exist",
        "tool_efficiency": "unavailable without reliable SDK usage",
    }
    comparison = None
    if compare_ids:
        compared = [store.load(item) for item in compare_ids]
        compatible = all(
            item.config_fingerprint == record.config_fingerprint
            and item.branch == record.branch
            for item in compared
        )
        comparison = {"status": "available" if compatible else "unavailable"}
        if not compatible:
            comparison["reason"] = "revision, branch, or configuration evidence differs"
    return EvaluationReport(
        record.id,
        str(results.get("verdict", record.phase)),
        observed,
        judgments,
        _record_evidence(record),
        tuple(unavailable),
        comparison,
    )


__all__ = ["EvaluationReport", "RunStateError", "evaluate_run"]
