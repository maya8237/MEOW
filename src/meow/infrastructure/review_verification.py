"""Current, durable deterministic verification for local reviews.

Native verdicts must require ``passed`` and, when bound to a review, the same
``key``. A live job, missing result, stale checkout or interrupted worker never
passes. Locks protect only reservation and publication, never running checks.
"""

import asyncio
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, is_dataclass, replace
from pathlib import Path
from uuid import uuid4

from meow.common.process import process_identity
from meow.execution.run_state import CheckResult
from meow.infrastructure.background import detach_command
from meow.infrastructure.checks import (
    _code_paths,
    cached_workflow_results,
    check_identity,
    checks_current,
    code_revision,
    configured_checks,
    run_final_checks,
)
from meow.infrastructure.lint import LintGateEvidence
from meow.project.config import config_paths, load_config
from meow.project.plan_files import is_review_report

_STARTUP_GRACE = 10


def _normalized(config: dict) -> dict:
    return {
        **config,
        "lint": config.get("lint", []),
        "lint_timeout": config.get("lint_timeout", 60),
        "tester": {"tests": [], **config.get("tester", {})},
        "build": config.get("build", []),
    }


def _json_value(value):
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot fingerprint {type(value).__name__}")


def _signature(root: Path, config: dict) -> dict:
    relevant = {
        name: config.get(name)
        for name in ("lint", "lint_timeout", "tester", "build", "permissions")
    }
    digest = hashlib.sha256(
        json.dumps(relevant, default=_json_value, sort_keys=True).encode()
    )
    for path in config_paths(root):
        digest.update(str(path.resolve()).encode())
        digest.update(path.read_bytes())
    signature = {
        "revision": code_revision(
            root,
            paths=tuple(
                path
                for path in _code_paths(root)
                if not is_review_report(
                    path, root / config.get("docs_dir", ".meow/plans")
                )
            ),
        ),
        "fingerprint": digest.hexdigest(),
        "identities": [check_identity(check) for check in configured_checks(config)],
    }
    signature["key"] = hashlib.sha256(
        json.dumps(signature, sort_keys=True).encode()
    ).hexdigest()
    return signature


def _state_path(root: Path) -> Path:
    return root / ".meow" / "review-checks" / "state.json"


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _live(state: dict) -> bool:
    if state.get("status") not in {"starting", "running"}:
        return False
    pid, identity = state.get("pid"), state.get("start_identity")
    if isinstance(pid, int) and identity:
        return process_identity(pid) == identity
    return time.time() - state.get("started_at", 0) < _STARTUP_GRACE


def _result_evidence(results: list[CheckResult]) -> LintGateEvidence:
    blocking, informational = [], []
    for result in results:
        if not result.passed:
            reason = "Timed out" if result.timed_out else f"Exit: {result.exit_code}"
            message = f"{result.kind}: $ {result.command}\n{reason}\n{result.output}"
            (blocking if result.required else informational).append(message)
    return LintGateEvidence(tuple(blocking), tuple(informational))


def _status(root: Path, config: dict, signature: dict, state: dict) -> dict:  # ruff: ignore[too-many-return-statements, complex-structure] -- decode durable job states fail-closed
    status = {
        **state,
        **signature,
        "state_path": str(_state_path(root)),
        "current": state.get("key") == signature["key"],
        "passed": False,
        "active": _live(state),
        "blocking": [],
        "informational": [],
    }
    if not signature["identities"]:
        return {**status, "status": "passed", "current": True, "passed": True}
    if not state:
        return {**status, "status": "missing"}
    if not status["current"]:
        return {**status, "status": "stale"}
    if state.get("status") in {"starting", "running"}:
        return {
            **status,
            "status": state["status"] if status["active"] else "interrupted",
        }
    record = _read(Path(state["result_path"])) if state.get("result_path") else {}
    results = [CheckResult.from_record(item) for item in record.get("results", [])]
    if record.get("job_id") != state.get("job_id") or any(r is None for r in results):
        return {**status, "status": "interrupted"}
    evidence = _result_evidence(results)
    status.update(
        blocking=list(evidence.blocking), informational=list(evidence.informational)
    )
    if record.get("error"):
        status["blocking"].append(record["error"])
    ready = checks_current(
        results,
        configured_checks(config),
        root,
        signature["revision"],
        fingerprint=signature["fingerprint"],
    )
    status["passed"] = (
        state.get("status") == "passed" and ready and not status["blocking"]
    )
    status["status"] = "passed" if status["passed"] else "failed"
    return status


def review_check_status(root: Path, config: dict) -> dict:
    """Inspect current evidence without launching commands; ``passed`` is the gate."""
    root, config = root.resolve(), _normalized(config)
    return _status(root, config, _signature(root, config), _read(_state_path(root)))


def _reserve(root: Path, config: dict, *, retry_failed=True) -> tuple[dict, bool]:
    from meow.ui.sessions import _atomic_json, _file_transaction

    signature = _signature(root, config)
    path = _state_path(root)
    with _file_transaction(path.with_suffix(".lock"), "review checks"):
        status = _status(root, config, signature, _read(path))
        if status["passed"] or status["active"]:
            return status, False
        if not retry_failed and status["current"] and status["status"] == "failed":
            return status, False
        job_id = uuid4().hex
        state = {
            **signature,
            "job_id": job_id,
            "status": "starting",
            "started_at": time.time(),
            "result_path": str(path.parent / f"{job_id}.json"),
            "log_path": str(path.parent / f"{job_id}.log"),
        }
        _atomic_json(path, state)
    return state, True


def _update(root: Path, job: dict) -> None:
    from meow.ui.sessions import _atomic_json, _file_transaction

    path = _state_path(root)
    with _file_transaction(path.with_suffix(".lock"), "review checks"):
        if _read(path).get("job_id") == job["job_id"]:
            _atomic_json(path, job)


def _evidence(status: dict) -> LintGateEvidence:
    blocking = status.get("blocking", [])
    if not status["passed"] and not blocking:
        blocking = [
            f"Review verification is {status['status']}; "
            "current passing evidence is required."
        ]
    return LintGateEvidence(tuple(blocking), tuple(status.get("informational", [])))


async def _execute(root: Path, config: dict, job: dict) -> None:  # ruff: ignore[too-many-statements]
    from meow.ui.sessions import _atomic_json

    job.update(
        status="running", pid=os.getpid(), start_identity=process_identity(os.getpid())
    )
    _update(root, job)
    results, error = [], ""
    with Path(job["log_path"]).open("a", encoding="utf-8") as stream:
        stream.write("Starting configured review verification.\n")
    try:
        results = await run_final_checks(root, config, fingerprint=job["fingerprint"])
        # New untracked check output can be adopted by the existing runner, but
        # edits to input source/config still invalidate the completed evidence.
        signature = _signature(root, config)
        # The runner validated the complete checkout. Review reports under
        # docs_dir are runtime output and must not invalidate themselves.
        complete_revision = code_revision(root)
        if all(result.revision == complete_revision for result in results):
            results = [
                replace(result, revision=signature["revision"]) for result in results
            ]
        ready = checks_current(
            results,
            configured_checks(config),
            root,
            signature["revision"],
            fingerprint=signature["fingerprint"],
        )
        job["status"] = "passed" if ready else "failed"
        if signature["fingerprint"] != job["fingerprint"]:
            job["status"] = "stale"
        job.update(signature)
    except Exception as exc:
        error, job["status"] = f"Review verification failed: {exc}", "failed"
    except BaseException:
        error, job["status"] = "Review verification was interrupted", "interrupted"
        raise
    finally:
        record = {
            **job,
            "results": [asdict(result) for result in results],
            "error": error,
        }
        _atomic_json(Path(job["result_path"]), record)
        with Path(job["log_path"]).open("a", encoding="utf-8") as stream:
            for result in results:
                stream.write(
                    f"{result.kind}: {result.command}\n{result.output}\n"
                    f"Exit: {result.exit_code}\n"
                )
            stream.write(f"{error}\nReview verification: {job['status']}\n")
        _update(root, job)


async def review_evidence(
    root: Path, config: dict, *, retry_failed=True
) -> LintGateEvidence:
    """Run or reuse deterministic lint/test/build checks for a local review."""
    cached = await cached_workflow_results(root, config)
    if cached is not None:
        return _result_evidence(cached)
    root, config = root.resolve(), _normalized(config)
    job, reserved = _reserve(root, config, retry_failed=retry_failed)
    if reserved:
        await _execute(root, config, job)
    return _evidence(review_check_status(root, config))


def start_review_checks(project_root: Path, active_dir: Path) -> dict:
    """Reserve a detached native job, reusing unchanged successful evidence."""
    from meow.ui.sessions import _atomic_json

    root = active_dir.resolve()
    config = _normalized(load_config(project_root))
    job, reserved = _reserve(root, config)
    if reserved:
        command = [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0,sys.argv[1]); from pathlib import Path; "
            "from meow.infrastructure.review_verification import worker_main; "
            "worker_main(Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4])",
            str(Path(__file__).resolve().parents[2]),
            str(project_root.resolve()),
            str(root),
            job["job_id"],
        ]
        try:
            process = detach_command(root, command, job["log_path"])
            _register_worker(root, job, process["pid"])
        except OSError as exc:
            job["status"] = "failed"
            _atomic_json(
                Path(job["result_path"]), {**job, "results": [], "error": str(exc)}
            )
            _update(root, job)
    return review_check_status(root, config)


def _register_worker(root: Path, job: dict, pid: int) -> None:
    from meow.ui.sessions import _atomic_json, _file_transaction

    path = _state_path(root)
    with _file_transaction(path.with_suffix(".lock"), "review checks"):
        state = _read(path)
        if state.get("job_id") == job["job_id"] and state.get("status") == "starting":
            state.update(pid=pid, start_identity=process_identity(pid))
            _atomic_json(path, state)


def worker_main(project_root: Path, active_dir: Path, job_id: str) -> None:
    """Internal detached entry point; package path is supplied by the launcher."""
    job = _read(_state_path(active_dir))
    if job.get("job_id") != job_id or job.get("status") != "starting":
        return
    config = _normalized(load_config(project_root))
    if _signature(active_dir, config)["key"] != job["key"]:
        job["status"] = "stale"
        _update(active_dir, job)
        return
    asyncio.run(_execute(active_dir, config, job))
