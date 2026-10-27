"""The managed-process contract: what a managing agent reads after a command.

Exit codes: 0 settled success, 75 parked or retryable (one stderr line
`MEOW_RETRY {...}`), 1 blocking or budget exhausted (`MEOW_BLOCKED {...}` with a
`stop` field), 3 pushed but the PR/MR failed to open, 130 interrupted.
`meow status --json` reports the same decision for any saved run.
"""

import json
import shlex
import sys
import time
from dataclasses import replace
from pathlib import Path

from meow.cli.resume_cli import _EXHAUSTED, budget_refusal
from meow.execution.run_failures import (
    BLOCKING_CAUSES,
    classify_failure,
    next_command,
    recognised_transient,
)
from meow.execution.run_state import (
    PARKED_PHASE,
    TERMINAL_PHASES,
    RunRecord,
    RunStateError,
    RunStore,
)
from meow.project.config import run_config
from meow.project.config_schema import RUN_DEFAULTS

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_PR_FAILED = 3
EXIT_TEMPFAIL = 75
EXIT_INTERRUPTED = 130
DEFAULT_RETRY_AFTER = 30
# A failure with no run record and no known-transient class: not retried.
UNRECOGNISED_CAUSE = "unrecognised_failure"
_SUCCESS_PHASES = frozenset({"complete", "delivered", "review_finished"})
_SETTLED_PHASES = TERMINAL_PHASES | {PARKED_PHASE, "interrupted_mutation"}
# Causes that stop a run without being a configuration/environment block.
_STOPS = frozenset({"no_progress", "exhausted", "cancelled"})


def _config(record: RunRecord) -> dict:
    try:
        return run_config(Path(record.repo))
    except (OSError, ValueError):
        return dict(RUN_DEFAULTS)


def _failure(record: RunRecord) -> dict:
    failure = record.results.get("failure")
    return failure if isinstance(failure, dict) else {}


def failure_cause(record: RunRecord) -> str | None:
    """The cause of the latest park or failure, if one was classified."""
    cause = _failure(record).get("cause")
    if cause is None and record.phase == PARKED_PHASE:
        cause = (record.park or {}).get("cause")
    if cause is None and record.phase in {"exhausted", "cancelled"}:
        cause = record.phase
    return cause if isinstance(cause, str) else None


def _retryable_phase(record: RunRecord) -> bool:
    if record.phase in {PARKED_PHASE, "interrupted_mutation"}:
        return (record.park or {}).get("kind") != "blocking"
    if record.phase == "failed":
        failure = _failure(record)
        return bool(failure) and (
            failure.get("kind") != "blocking"
            and failure.get("cause") not in BLOCKING_CAUSES
        )
    return False


def _continuation(record: RunRecord) -> str | None:
    """`next_command` as if parked, for any retryable phase."""
    return next_command(replace(record, phase=PARKED_PHASE))


def not_resumed_reason(record: RunRecord, config: dict) -> str | None:
    """Why a settled, unsuccessful run will not be resumed; None otherwise."""
    if record.phase in _SUCCESS_PHASES or record.phase not in _SETTLED_PHASES:
        return None
    if not _retryable_phase(record):
        cause = failure_cause(record) or record.phase
        return cause if cause in _STOPS else f"blocking: {cause}"
    return budget_refusal(replace(record, phase=PARKED_PHASE), config)


def budget_left(record: RunRecord, config: dict) -> int:
    """Manager resumes still allowed under both resume caps."""
    park = record.park if isinstance(record.park, dict) else {}
    return max(
        0,
        min(
            config["managed_resumes"] - int(park.get("manager_resumes", 0)),
            config["max_total_resumes"] - int(park.get("total_resumes", 0)),
        ),
    )


def heartbeat_age(record: RunRecord) -> float | None:
    beat = record.results.get("heartbeat_at")
    if not isinstance(beat, (int, float)) or isinstance(beat, bool):
        return None
    return round(max(0.0, time.time() - beat), 1)


def status_payload(record: RunRecord, worker: str) -> dict:
    """The `meow status --json` document for one run record."""
    config = _config(record)
    reason = not_resumed_reason(record, config)
    retryable = _retryable_phase(record) and reason is None
    park = record.park if isinstance(record.park, dict) else {}
    payload = {
        "id": record.id,
        "phase": record.phase,
        "settled": record.phase in _SETTLED_PHASES,
        "retryable": retryable,
        "last_failure": record.last_failure,
        "cause": failure_cause(record),
        "next_command": _continuation(record) if retryable else None,
        "attempts": int(park.get("attempts", 0)),
        "budget_left": budget_left(record, config),
        "worker": worker,
        "intent": record.results.get("intent"),
        "heartbeat_age": heartbeat_age(record),
        "retry_after": park.get("retry_after") if retryable else None,
    }
    if reason is not None:
        payload["not_resumed_reason"] = reason
    return payload


def _emit(prefix: str, payload: dict) -> None:
    print(f"{prefix} {json.dumps(payload)}", file=sys.stderr, flush=True)


def _retry(run_id: str | None, command: str | None, retry_after: object) -> int:
    seconds = (
        retry_after
        if isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool)
        else DEFAULT_RETRY_AFTER
    )
    _emit(
        "MEOW_RETRY",
        {"run_id": run_id, "next_command": command, "retry_after": seconds},
    )
    return EXIT_TEMPFAIL


def _blocked(run_id: str | None, cause: str, message: str, stop: str) -> int:
    _emit(
        "MEOW_BLOCKED",
        {"run_id": run_id, "cause": cause, "message": message, "stop": stop},
    )
    return EXIT_BLOCKED


def record_exit(record: RunRecord) -> int:
    """Map a run's saved outcome to the exit code, printing its contract line."""
    if record.phase in _SUCCESS_PHASES:
        delivery = record.delivery if isinstance(record.delivery, dict) else {}
        failed_pr = delivery.get("pushed") and delivery.get("pr_error")
        return EXIT_PR_FAILED if failed_pr else EXIT_OK
    config = _config(record)
    reason = not_resumed_reason(record, config)
    message = record.last_failure or record.phase
    if record.phase not in _SETTLED_PHASES:  # left active: continue it
        return _retry(record.id, _continuation(record), None)
    if reason is None and _retryable_phase(record):
        retry_after = (record.park or {}).get("retry_after")
        return _retry(record.id, _continuation(record), retry_after)
    cause = failure_cause(record) or record.phase
    if reason in _EXHAUSTED:
        stop = "budget_exhausted"
    elif _retryable_phase(record):  # retryable, but refused (plan-only, source)
        stop = "not_resumable"
    else:
        stop = cause if cause in _STOPS else "blocking"
    detail = f"{message} ({reason})" if reason else message
    return _blocked(record.id, cause, detail, stop)


def outcome_exit(
    working_dir: Path,
    run_id: str | None,
    exc: BaseException | None,
    argv: list[str],
) -> int:
    """Exit code for a run-driving command, from its run record when known.

    Without a record no budget bounds a retry, so only a recognised
    transient failure exits 75 (the original command as `next_command`);
    anything else exits 1 with `MEOW_BLOCKED`.
    """
    if run_id is not None:
        try:
            return record_exit(RunStore(working_dir).load(run_id))
        except RunStateError:
            pass
    if exc is None:
        return EXIT_OK
    return _record_free_exit(run_id, exc, argv)


def _record_free_exit(run_id: str | None, exc: BaseException, argv: list[str]) -> int:
    kind, cause = classify_failure(exc)
    if kind != "blocking" and recognised_transient(exc):
        return _retry(run_id, shlex.join(["meow", *argv]), None)
    if isinstance(exc, FileNotFoundError):  # a missing plan/config input
        cause = "invalid_input"
    elif kind != "blocking":
        cause = UNRECOGNISED_CAUSE
    return _blocked(run_id, cause, str(exc) or type(exc).__name__, "blocking")


def finished_exit(working_dir: Path, run_id: str) -> int:
    """Exit code once a run command returned normally: its settled outcome
    (3 when pushed but the PR/MR failed), else 0."""
    try:
        record = RunStore(working_dir).load(run_id)
    except RunStateError:
        return EXIT_OK
    return record_exit(record) if record.phase in _SETTLED_PHASES else EXIT_OK
