"""Atomic, deliberately small run journal shared by CLI and native flows."""

import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from meow.common.atomic import replace_with_retry
from meow.common.process import process_identity
from meow.infrastructure.logging import get_logger

logger = get_logger(__name__)

MAX_OUTPUT = 8000
_DEFAULT_AUTO_RETRIES = 3
_DEFAULT_STARTUP_GRACE = 180
ERROR_LOG_TAIL_LINES = 20
SCHEMA_VERSION = 1
# Phases after which a run no longer drives work. The single source for worker
# reconciliation, cancellation, and plan-ownership checks.
TERMINAL_PHASES = frozenset({
    "complete",
    "delivered",
    "delivery_failed",
    "exhausted",
    "failed",
    "cancelled",
    "review_finished",
})
# Phases that mean work is under way again: any earlier failure is stale.
_ACTIVE_PHASES = frozenset({
    "resuming",
    "preparing",
    "planning",
    "generator_started",
    "reviewer_started",
    "checking",
    "tasks_waiting",
    "pipeline_waiting",
    "pipeline_fixing",
})
# Parked runs stopped on a retryable failure: settled (no driver owns them) but
# not terminal, since `next_command` continues them.
PARKED_PHASE = "parked"
# Phases a dead driver must not overwrite: they already say why work stopped.
_RECONCILE_SKIP = TERMINAL_PHASES | {PARKED_PHASE, "interrupted_mutation"}
_SECRET_KEY = re.compile(
    r"(?:token|secret|password|credential|api.?key|authorization)", re.I
)
_SECRET_VALUE = re.compile(
    r"(?i)\b(token|secret|password|api[_-]?key)\s*[:=]\s*[^\s,;]+"
)
_BEARER_VALUE = re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+\S+")
_ID = re.compile(r"[0-9a-f]{32}\Z")


class RunStateError(ValueError):
    """A run cannot be loaded safely; no agent should be started."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe(value: object, key: str = "") -> object:  # ruff: ignore[complex-structure, too-many-return-statements, too-many-branches]
    if (
        key
        in {
            "tokens",
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        }
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        return value
    if _SECRET_KEY.search(key):
        return "[redacted]"
    if value is None and key == "usage":
        return "unavailable"
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(k): _safe(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        cleaned = _SECRET_VALUE.sub(lambda m: f"{m[1]}=[redacted]", value)
        cleaned = _BEARER_VALUE.sub("Authorization: Bearer [redacted]", cleaned)
        return cleaned[:MAX_OUTPUT] if key == "output" else cleaned
    if isinstance(value, (int, float, bool)):
        return value
    return str(value)


def _mtime(path: Path) -> int:
    """Modification time; a record deleted mid-listing sorts oldest."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


@dataclass(frozen=True)
class CheckResult:
    kind: str
    command: str
    required: bool
    exit_code: int | None
    duration: float
    output: str
    revision: str
    config_fingerprint: str
    timed_out: bool = False
    identity: str = ""

    @property
    def passed(self) -> bool:
        return not self.timed_out and self.exit_code == 0

    @classmethod
    def from_record(cls, item: object) -> "CheckResult | None":
        """Rebuild a stored result; None when the schema drifted (the check
        then simply runs again instead of failing the resume)."""
        if not isinstance(item, dict):
            return None
        names = {field.name for field in fields(cls)}
        try:
            return cls(**{key: value for key, value in item.items() if key in names})
        except TypeError:
            return None


@dataclass
class RunRecord:
    id: str
    source: str
    request: str
    repo: str
    worktree: str
    branch: str
    created_at: str
    updated_at: str
    schema_version: int = SCHEMA_VERSION
    sessions: dict[str, str] = field(default_factory=dict)
    phase: str = "created"
    attempt: int = 1
    round: int = 0
    plan_file: str | None = None
    plan_fingerprint: str | None = None
    config_fingerprint: str | None = None
    review_file: str | None = None
    last_failure: str | None = None
    results: dict = field(default_factory=dict)
    transitions: list[dict] = field(default_factory=list)
    usage: object = "unavailable"
    delivery: dict = field(default_factory=dict)
    background: dict = field(default_factory=dict)
    park: dict = field(default_factory=dict)
    driver: dict = field(default_factory=dict)
    ui_session_id: str | None = None
    ui_attempt_id: str | None = None


class RunStore:  # ruff: ignore[too-many-public-methods]
    def __init__(self, repo: Path):
        self.directory = Path(repo).resolve() / ".meow" / "runs"

    def _path(self, run_id: str) -> Path:
        if not _ID.fullmatch(run_id):
            raise RunStateError(
                "Unknown run ID. Inspect with meow status in "
                f"{self.directory.parent.parent}."
            )
        return self.directory / f"{run_id}.json"

    def _write(self, record: RunRecord) -> RunRecord:
        """Persist atomically; return the record exactly as stored."""
        self.directory.mkdir(parents=True, exist_ok=True)
        stored = _safe(asdict(record))
        payload = json.dumps(stored, indent=2, sort_keys=True) + "\n"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.directory,
                prefix=f"{record.id}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            replace_with_retry(temporary, self._path(record.id))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return RunRecord(**stored)

    def create(  # ruff: ignore[too-many-arguments]
        self,
        *,
        source: str,
        request: str,
        repo: Path,
        worktree: Path,
        branch: str,
        delivery: dict | None = None,
        ui_session_id: str | None = None,
        ui_attempt_id: str | None = None,
    ) -> RunRecord:
        repo = Path(repo).resolve()
        if (ui_session_id is None) != (ui_attempt_id is None):
            raise RunStateError("UI session and attempt IDs must be supplied together")
        if ui_session_id is None:
            context = {
                "session": os.environ.get("MEOW_UI_SESSION_ID"),
                "attempt": os.environ.get("MEOW_UI_ATTEMPT_ID"),
                "repo": os.environ.get("MEOW_UI_REPO"),
            }
            if any(value is not None for value in context.values()):
                if (
                    any(value is None for value in context.values())
                    or not _ID.fullmatch(context["session"] or "")
                    or not _ID.fullmatch(context["attempt"] or "")
                    or Path(context["repo"] or "").resolve() != repo
                ):
                    raise RunStateError("Invalid UI worker run association context")
                ui_session_id = context["session"]
                ui_attempt_id = context["attempt"]
        if ui_session_id is not None and (
            not isinstance(ui_session_id, str)
            or not isinstance(ui_attempt_id, str)
            or not _ID.fullmatch(ui_session_id)
            or not _ID.fullmatch(ui_attempt_id)
        ):
            raise RunStateError("Invalid UI session or attempt ID")
        now = _now()
        record = RunRecord(
            uuid4().hex,
            source,
            request,
            str(repo),
            str(Path(worktree).resolve()),
            branch,
            now,
            now,
            transitions=[{"phase": "created", "at": now}],
            delivery=_safe(delivery or {}, "delivery"),
            ui_session_id=ui_session_id,
            ui_attempt_id=ui_attempt_id,
        )
        self._write(record)
        return self.load(record.id)

    def load(  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
        self, run_id: str
    ) -> RunRecord:
        path = self._path(run_id)
        if not path.is_file():
            raise RunStateError(f"Run {run_id} is missing. Inspect with meow status.")
        try:
            raw = _safe(json.loads(path.read_text(encoding="utf-8")))
            if not isinstance(raw, dict):
                raise ValueError("record must be an object")
            schema_version = raw.get("schema_version", SCHEMA_VERSION)
            if schema_version != SCHEMA_VERSION:
                raise ValueError("unsupported schema version")
            raw.setdefault("schema_version", SCHEMA_VERSION)
            raw.setdefault("sessions", {})
            if not isinstance(raw["sessions"], dict) or any(
                not isinstance(role, str)
                or not role
                or not isinstance(session_id, str)
                or not session_id
                for role, session_id in raw["sessions"].items()
            ):
                raise ValueError("invalid sessions")
            record = RunRecord(**raw)
            fields = (record.phase, record.repo, record.worktree, record.branch)
            if record.id != run_id:
                raise ValueError("invalid record")
            if (record.ui_session_id is None) != (record.ui_attempt_id is None) or (
                record.ui_session_id is not None
                and (
                    not _ID.fullmatch(record.ui_session_id)
                    or not _ID.fullmatch(record.ui_attempt_id or "")
                )
            ):
                raise ValueError("invalid UI association")
            if not isinstance(record.transitions, list):
                raise ValueError("invalid transitions")
            if not isinstance(record.results, dict):
                raise ValueError("invalid results")
            if not isinstance(record.park, dict) or not isinstance(record.driver, dict):
                raise ValueError("invalid park or driver")
            if not isinstance(record.schema_version, int) or isinstance(
                record.schema_version, bool
            ):
                raise ValueError("invalid schema version")
            if not isinstance(record.sessions, dict):
                raise ValueError("invalid sessions")
            if not all(isinstance(value, str) for value in fields):
                raise ValueError("invalid record")
            if (
                datetime.fromisoformat(record.created_at).tzinfo is None
                or datetime.fromisoformat(record.updated_at).tzinfo is None
            ):
                raise ValueError("timestamps require timezone")
            if not all(
                isinstance(item, dict)
                and isinstance(item.get("phase"), str)
                and isinstance(item.get("at"), str)
                for item in record.transitions
            ):
                raise ValueError("invalid transition history")
        except (OSError, ValueError, TypeError) as exc:
            raise RunStateError(
                f"Run {run_id} is corrupt. Preserve {path} and inspect with "
                "meow status."
            ) from exc
        return record

    def record_paths(self) -> list[Path]:
        """Run records only."""
        return list(self.directory.glob("*.json"))

    def latest(self) -> RunRecord:
        paths = sorted(self.record_paths(), key=_mtime)
        if not paths:
            raise RunStateError("No runs found. Start one with meow run.")
        return self.load(paths[-1].stem)

    def add_usage(self, run_id: str, entry: dict[str, object]) -> RunRecord:
        """Append a received SDK result without changing the workflow phase."""
        record = self.load(run_id)
        previous = record.usage if isinstance(record.usage, dict) else {}
        entries = previous.get("entries", [])
        if not isinstance(entries, list):
            entries = []
        record.usage = {"entries": [*entries, _safe({**entry, "at": _now()})]}
        record.updated_at = _now()
        return self._write(record)

    def set_session(self, run_id: str, role: str, session_id: str) -> RunRecord:
        """Retain only the Claude session reference needed for resume."""
        if not isinstance(role, str) or not role.strip():
            raise ValueError("session role must be a non-empty string")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session ID must be a non-empty string")
        record = self.load(run_id)
        role = role.strip().lower()
        if record.sessions.get(role) == session_id:
            return record
        record.sessions[role] = session_id
        record.updated_at = _now()
        return self._write(record)

    def set_driver(self, run_id: str) -> RunRecord:
        """Record the process now driving the run (read by liveness checks)."""
        record = self.load(run_id)
        record.driver = {
            "pid": os.getpid(),
            "start_identity": process_identity(os.getpid()),
            "host": "claude-code" if os.environ.get("CLAUDECODE") else "cli",
        }
        record.updated_at = _now()
        return self._write(record)

    def update_background(self, run_id: str, **patch: object) -> RunRecord:
        """Update worker metadata without changing the workflow phase."""
        record = self.load(run_id)
        record.background.update({
            key: _safe(value, key) for key, value in patch.items()
        })
        record.updated_at = _now()
        return self._write(record)

    def update_results(self, run_id: str, **patch: object) -> RunRecord:
        """Persist workflow progress without adding a phase transition."""
        record = self.load(run_id)
        record.results.update({key: _safe(value, key) for key, value in patch.items()})
        record.updated_at = _now()
        return self._write(record)

    def transition(  # ruff: ignore[complex-structure]
        self, run_id: str, phase: str, **patch: object
    ) -> RunRecord:
        record = self.load(run_id)
        if not phase or not isinstance(phase, str):
            raise ValueError("phase must be a non-empty string")
        unknown = set(patch) - set(RunRecord.__dataclass_fields__)
        if unknown or "id" in patch or "transitions" in patch:
            raise ValueError(f"Invalid run transition fields: {sorted(unknown)}")
        for key, value in patch.items():
            safe_value = _safe(value, key)
            if key == "results" and isinstance(safe_value, dict):
                record.results.update(safe_value)
            else:
                setattr(record, key, safe_value)
        if phase in _ACTIVE_PHASES or phase in TERMINAL_PHASES:
            # Only a parked run offers a continuation; `park` stays so the
            # no-progress budget survives the resume.
            record.results.pop("next_command", None)
        if "last_failure" not in patch and phase in _ACTIVE_PHASES:
            record.last_failure = None  # a resumed/progressing run is not failed
        record.phase = phase
        record.updated_at = _now()
        record.transitions.append({
            "phase": phase,
            "at": record.updated_at,
            "attempt": record.attempt,
            "round": record.round,
        })
        return self._write(record)

    def park(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
        self,
        run_id: str,
        reason: BaseException | str,
        kind: str,
        cause: str,
        retry_after: float | None = None,
    ) -> RunRecord:
        """Settle a retryable failure as `parked` with a `next_command`.

        `attempts` counts parks since the progress fingerprint last changed;
        past `[run] auto_retries` with no progress the run ends `failed` with
        cause `no_progress`, naming the failure that kept repeating.
        """
        from meow.execution.run_failures import next_command, progress_fingerprint
        from meow.project.config import run_config

        record = self.load(run_id)
        message = (
            reason if isinstance(reason, str) else str(reason) or type(reason).__name__
        )
        previous = record.park if isinstance(record.park, dict) else {}
        phase_before = (
            str(previous.get("phase_before") or "created")
            if record.phase == PARKED_PHASE
            else record.phase
        )
        fingerprint = progress_fingerprint(record)
        unchanged = bool(previous) and previous.get("fingerprint") == fingerprint
        attempts = int(previous.get("attempts", 0)) + 1 if unchanged else 1
        park = {
            "kind": kind,
            "cause": cause,
            "reason": message,
            "phase_before": phase_before,
            "attempts": attempts,
            "total_resumes": sum(
                1 for item in record.transitions if item.get("phase") == "resuming"
            ),
            "manager_resumes": int(previous.get("manager_resumes", 0)),
            "fingerprint": fingerprint,
            "parked_at": _now(),
            "retry_after": retry_after,
        }
        try:
            auto_retries = run_config(Path(record.repo))["auto_retries"]
        except (OSError, ValueError):
            auto_retries = _DEFAULT_AUTO_RETRIES
        if unchanged and attempts > auto_retries:
            park.update(
                kind="blocking",
                cause="no_progress",
                repeated_kind=kind,
                repeated_cause=cause,
            )
            return self.transition(
                run_id,
                "failed",
                park=park,
                results={"failure": {"kind": "blocking", "cause": "no_progress"}},
                last_failure=(
                    f"No progress: {cause} failure repeated {attempts} times "
                    f"at {phase_before} with nothing new: {message}"
                ),
            )
        parked = replace(record, phase=PARKED_PHASE)
        return self.transition(
            run_id,
            PARKED_PHASE,
            park=park,
            results={
                "failure": {"kind": kind, "cause": cause},
                "next_command": next_command(parked),
            },
            last_failure=message,
        )


def _owner_alive(info: dict) -> bool | None:
    """Whether the recorded process still runs; None when none was recorded."""
    pid, identity = info.get("pid"), info.get("start_identity")
    if not isinstance(pid, int) or not isinstance(identity, str) or not identity:
        return None
    return process_identity(pid) == identity


def startup_grace(repo: Path) -> float:
    """`[run] worker_startup_grace`: how long a launched worker may take to
    register before it counts as dead."""
    from meow.project.config import run_config

    try:
        return float(run_config(Path(repo))["worker_startup_grace"])
    except (OSError, ValueError):
        return _DEFAULT_STARTUP_GRACE


def run_owner_alive(record: RunRecord) -> bool | None:
    """True while a driver or background worker owns the run; False when every
    recorded owner died; None when no owner was ever recorded (native runs)."""
    owners: list[bool] = []
    background = record.background if isinstance(record.background, dict) else {}
    if background:
        alive = _owner_alive(background)
        if alive is None:  # launched, not registered yet
            started = background.get("launch_started_at")
            alive = not (
                isinstance(started, (int, float))
                and time.time() - started > startup_grace(Path(record.repo))
            )
        owners.append(alive)
    if (driver := driver_alive(record)) is not None:
        owners.append(driver)
    return any(owners) if owners else None


def driver_alive(record: RunRecord) -> bool | None:
    """Whether the recorded driver still runs; None when none was recorded.

    A driver whose birth identity could not be read counts as alive: unknown
    must never park (or be resumed over) a run that may still be driven.
    """
    driver = record.driver if isinstance(record.driver, dict) else {}
    if not driver:
        return None
    alive = _owner_alive(driver)
    if alive is None:
        return isinstance(driver.get("pid"), int)
    return alive


def error_log_tail(store: RunStore, run_id: str) -> str:
    """The last `ERROR_LOG_TAIL_LINES` lines of `<run_id>.error.log`."""
    try:
        text = (store.directory / f"{run_id}.error.log").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return "(no error log)"
    lines = text.strip().splitlines()[-ERROR_LOG_TAIL_LINES:]
    return "\n".join(lines) or "(error log empty)"


def park_crashed(store: RunStore, run_id: str) -> RunRecord | None:
    """Park a run whose driver died without settling it; None if settled."""
    record = store.load(run_id)
    if record.phase in _RECONCILE_SKIP:
        return None
    tail = error_log_tail(store, run_id)
    return store.park(
        run_id,
        f"driver died during {record.phase}: {tail}",
        "crashed",
        "driver_died",
    )


def reconcile_stale_runs(store: RunStore) -> list[str]:
    """Park every unsettled run whose driver and worker are both gone.

    Runs that never recorded an owner (native checkpoints) are left alone.
    Returns the IDs that were parked (or failed on an exhausted budget).
    """
    settled = []
    for path in sorted(store.record_paths()):
        try:
            record = store.load(path.stem)
            if record.phase in _RECONCILE_SKIP or run_owner_alive(record) is not False:
                continue
            if park_crashed(store, record.id) is not None:
                settled.append(record.id)
        except (RunStateError, OSError) as exc:
            logger.warning("run_reconcile_skipped", run=path.stem, reason=str(exc))
    return settled


def reconcile_quietly(repo: Path) -> list[str]:
    """`reconcile_stale_runs` for command entry: never blocks the command."""
    try:
        return reconcile_stale_runs(RunStore(repo))
    except (RunStateError, OSError, ValueError) as exc:
        logger.warning("run_reconcile_failed", reason=str(exc))
        return []
